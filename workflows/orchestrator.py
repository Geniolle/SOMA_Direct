from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation
from dataclasses import replace
from typing import List, Optional
from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import (
    AuditOutcome,
    ContaOrdemRow,
    OperationOutcome,
    TipoMovimento,
    clean_amount_for_comparison,
    format_amount_for_input,
    is_entrada_ou_saida,
    norm_basic,
    normalize_date_str,
)
from services.audit_service import AuditService
from services.duplicate_checker import DuplicateChecker
from services.sheets_service import GoogleSheetsService
from services.soma_api_service import SomaApiService

logger = logging.getLogger("soma_direct.orchestrator")


class DirectOrchestrator:
    """Orquestrador modular e rápido para o SOMA (Backend HTTP Direto)."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings.from_env()
        self.http = ResilientSession(
            timeout=self.settings.timeout_seconds,
            verify_tls=self.settings.verify_tls,
        )
        self.auth = SomaAuthenticator(self.settings, self.http)
        self.api = SomaApiService(self.settings, self.http)
        self.duplicate_checker = DuplicateChecker(self.api)
        self.sheets = GoogleSheetsService(self.settings)
        self.audit_service = AuditService(self.settings, self.http, self.sheets)

    def initialize(self) -> bool:
        """Autentica na sessão HTTP e carrega catálogos de apoio."""
        if not self.auth.login():
            raise RuntimeError("Não foi possível autenticar no SOMA.")
        self.api.load_catalogs()
        return True

    def process_row(self, row: ContaOrdemRow, dry_run: bool = False) -> OperationOutcome:
        """Processa uma única linha com verificação de duplicidade e execução direta."""
        logger.info(f"Processando linha {row.row_number} [{row.tipo.value}]: {row.descricao_soma or row.descricao} ({row.importancia} EUR)...")

        if dry_run:
            logger.info(f"[DRY-RUN] Simulação para linha {row.row_number}: Nenhuma alteração realizada.")
            return OperationOutcome(
                success=True,
                doc_id="SIMULADO",
                tipo=row.tipo.value,
                row_number=row.row_number,
                elapsed_ms=0,
                dados_doc="Execução simulada (dry-run)"
            )

        validation_error = self._validate_launch_row(row)
        if validation_error:
            outcome = OperationOutcome(False, "", row.tipo.value, row.row_number, 0, error_message=validation_error)
            self.sheets.mark_row_validation_error(row.row_number, validation_error)
            return outcome

        claim = self.sheets.claim_row(row.row_number)
        if not claim:
            return OperationOutcome(False, "", row.tipo.value, row.row_number, 0, error_message="Linha reservada por outro processo")

        try:
            return self._process_claimed_row(row)
        except Exception as exc:
            logger.exception("Falha ao processar linha %s", row.row_number)
            self.sheets.mark_row_failed(row.row_number, str(exc))
            return OperationOutcome(False, "", row.tipo.value, row.row_number, 0, error_message=str(exc))

    @staticmethod
    def _validate_launch_row(row: ContaOrdemRow) -> Optional[str]:
        required = {
            "DATA MOV.": row.data_mov,
            "TIPO": row.tipo.value if is_entrada_ou_saida(row.tipo) else "",
            "IMPORTÂNCIA": row.importancia,
            "PLANO DE CONTA": row.plano_conta,
            "CENTRO DE CUSTO": row.centro_custo,
            "DESCRIÇÃO SOMA": row.descricao_soma,
            "CAIXA": row.caixa,
            "FORMA DE PAGAMENTO": row.forma_pagamento,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            return "Campos obrigatórios ausentes: " + ", ".join(missing)
        try:
            amount = Decimal(format_amount_for_input(row.importancia).replace(",", "."))
            if amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            return f"IMPORTÂNCIA inválida: '{row.importancia}'"
        return None

    def _confirm_and_settle(self, row: ContaOrdemRow, doc_id: str) -> Optional[str]:
        record = self.audit_service.search_by_codigo(doc_id)
        if not record:
            return "Documento não localizado por código após o lançamento"
        if "EM ABERTO" in (record.status or "").upper() or str(record.baixa or "").strip().upper() != "SIM":
            if not self.audit_service.insert_soma_payment(
                doc_id=doc_id,
                data_pagamento=row.data_mov,
                valor=row.importancia,
                caixa_str=row.caixa,
                forma_str=row.forma_pagamento,
            ):
                return "Documento criado, mas pagamento/baixa não foi confirmado no SOMA"
            record = self.audit_service.search_by_codigo(doc_id)
        validation_row = replace(row, doc_soma=doc_id)
        status, inconsistencies = self.audit_service.validate_soma_record(validation_row, record)
        if status != "Confirmado":
            return "; ".join(inconsistencies) or "Documento criado, mas não passou na conferência final"
        return None

    def _process_claimed_row(self, row: ContaOrdemRow) -> OperationOutcome:
        # 1. Pesquisa preventiva estrita por DATA MOV. + DESCRIÇÃO SOMA.
        candidates = [
            item for item in self.audit_service.search_by_descricao(row.descricao_soma, data_mov=row.data_mov)
            if norm_basic(item.descricao) == norm_basic(row.descricao_soma)
            and normalize_date_str(item.data) == normalize_date_str(row.data_mov)
        ]
        if len(candidates) > 1:
            self.sheets.mark_row_duplicate(row.row_number, len(candidates))
            message = f"Pesquisa encontrou {len(candidates)} registros com a mesma data e descrição"
            logger.error("Linha %s: %s", row.row_number, message)
            return OperationOutcome(False, "Analisar", row.tipo.value, row.row_number, 0, error_message=message)

        if len(candidates) == 1:
            existing = candidates[0]
            existing_doc = existing.codigo
            mismatches = []
            if clean_amount_for_comparison(existing.valor) != clean_amount_for_comparison(row.importancia):
                mismatches.append(f"VALOR site='{existing.valor}' != sheet='{row.importancia}'")
            if norm_basic(existing.tipo) != norm_basic(row.tipo.value):
                mismatches.append(f"TIPO site='{existing.tipo}' != sheet='{row.tipo.value}'")
            if mismatches:
                message = "; ".join(mismatches)
                self.sheets.mark_row_validation_error(row.row_number, message)
                return OperationOutcome(False, "Analisar", row.tipo.value, row.row_number, 0, error_message=message)

            paid = norm_basic(existing.status) == "pago" and norm_basic(existing.baixa) == "sim"
            if not paid and "em aberto" in norm_basic(existing.status):
                paid = self.audit_service.insert_soma_payment(
                    doc_id=existing_doc,
                    data_pagamento=row.data_mov,
                    valor=row.importancia,
                    caixa_str=row.caixa,
                    forma_str=row.forma_pagamento,
                )
                existing = self.audit_service.search_by_codigo(existing_doc)
                paid = bool(
                    paid and existing
                    and norm_basic(existing.status) == "pago"
                    and norm_basic(existing.baixa) == "sim"
                )
            if not paid:
                message = f"Documento {existing_doc} existe, mas não ficou PAGO com baixa SIM"
                self.sheets.mark_row_validation_error(row.row_number, message)
                return OperationOutcome(False, "Analisar", row.tipo.value, row.row_number, 0, error_message=message)

            dados_doc = self.audit_service.fetch_dados_doc(existing_doc)
            logger.info(f"-> Documento existente confirmado no SOMA com DOC {existing_doc}. Atualizando planilha...")
            self.sheets.mark_row_completed(
                row_idx=row.row_number,
                doc_id=existing_doc,
                dados_doc=dados_doc or f"Documento recuperado do SOMA ({existing_doc})",
                elapsed_ms=0
            )
            return OperationOutcome(
                success=True,
                doc_id=existing_doc,
                tipo=row.tipo.value,
                row_number=row.row_number,
                elapsed_ms=0,
                dados_doc="Documento existente recuperado"
            )

        # 2. Nenhum registro encontrado: criação autorizada.
        if row.tipo == TipoMovimento.SAIDA:
            outcome = self.api.criar_saida(row)
        elif row.tipo == TipoMovimento.ENTRADA:
            outcome = self.api.criar_entrada(row)
        else:
            outcome = self.api.criar_transferencia(row)

        # 3. Atualização na planilha Google Sheets
        if outcome.success:
            confirmation_error = self._confirm_and_settle(row, outcome.doc_id)
            if confirmation_error:
                outcome.success = False
                outcome.error_message = confirmation_error
                self.sheets.mark_row_failed(row.row_number, confirmation_error)
                return outcome
            logger.info(f"-> SUCESSO Linha {row.row_number}: DOC. SOMA={outcome.doc_id} em {outcome.elapsed_ms}ms")
            self.sheets.mark_row_completed(
                row_idx=row.row_number,
                doc_id=outcome.doc_id,
                dados_doc=outcome.dados_doc,
                elapsed_ms=outcome.elapsed_ms
            )
        else:
            logger.error(f"-> ERRO Linha {row.row_number}: {outcome.error_message}")
            self.sheets.mark_row_failed(row.row_number, outcome.error_message)

        return outcome

    def run_target_rows(self, row_indices: List[int], dry_run: bool = False) -> List[OperationOutcome]:
        """Executa uma lista de linhas especificadas por índice."""
        self.initialize()

        outcomes = []
        overall_t0 = time.perf_counter()

        for idx in row_indices:
            row = self.sheets.get_row(idx)
            if not row:
                logger.error(f"Linha {idx} não encontrada na planilha!")
                continue
            if not is_entrada_ou_saida(row.tipo):
                logger.warning(f"Linha {idx} ignorada: TIPO '{row.tipo.value}' não é Entrada ou Saída.")
                continue
            outcomes.append(self.process_row(row, dry_run=dry_run))

        total_ms = int((time.perf_counter() - overall_t0) * 1000)
        logger.info(f"=== BATCH FINALIZADO: {len(outcomes)} linhas processadas em {total_ms/1000:.2f}s ({total_ms}ms) ===")
        return outcomes

    def run_pending(self, limit: Optional[int] = None, dry_run: bool = False) -> List[OperationOutcome]:
        """Varre a planilha CONTAORDEM e processa todos os registros pendentes."""
        self.initialize()
        logger.info("Buscando registros pendentes na planilha...")
        all_rows = self.sheets.get_all_rows(only_entrada_saida=True)

        pending = []
        for r in all_rows:
            doc = (r.doc_soma or "").strip().upper()
            processing = r.status.upper().startswith("EM PROCESSAMENTO")
            stale = False
            if processing:
                parts = r.status.split(":", 2)
                try:
                    stale = len(parts) >= 2 and time.time() - int(parts[1]) > self.settings.claim_stale_seconds
                except ValueError:
                    stale = False
            if (not doc or doc == "EM ERRO") and (not processing or stale):
                pending.append(r)

        if limit and limit > 0:
            pending = pending[:limit]

        logger.info(f"Total de registros pendentes identificados: {len(pending)}")
        outcomes = []
        overall_t0 = time.perf_counter()

        for r in pending:
            outcomes.append(self.process_row(r, dry_run=dry_run))

        total_ms = int((time.perf_counter() - overall_t0) * 1000)
        logger.info(f"=== BATCH PENDENTES FINALIZADO: {len(outcomes)} linhas em {total_ms/1000:.2f}s ===")
        return outcomes

    def audit_target_rows(self, row_indices: List[int], dry_run: bool = False) -> List[AuditOutcome]:
        """Audita uma lista de linhas especificadas."""
        self.auth.login()
        outcomes: List[AuditOutcome] = []
        updates = []

        for idx in row_indices:
            row = self.sheets.get_row(idx)
            if not row:
                logger.error(f"Linha {idx} não encontrada na planilha!")
                continue
            if not is_entrada_ou_saida(row.tipo):
                logger.warning(f"Linha {idx} ignorada: TIPO '{row.tipo.value}' não é Entrada ou Saída.")
                continue
            logger.info(f"Auditando Linha {idx}: DOC={row.doc_soma} [{row.tipo.value}]...")
            outcome = self.audit_service.audit_row(row)
            outcomes.append(outcome)

            status_str = "ERRO" if outcome.inconsistent and "DADOS DOC" in "; ".join(outcome.inconsistencies) else None
            aud_str = "Confirmado" if outcome.confirmed else ("Corrigido" if outcome.corrected else "Inconsistente")

            updates.append({
                "row_idx": idx,
                "auditoria": aud_str,
                "new_doc": outcome.new_doc,
                "new_desc": outcome.new_desc,
                "dados_doc": outcome.dados_doc if outcome.dados_doc != row.dados_doc else None,
                "status": status_str,
            })

        if not dry_run and updates:
            self.sheets.batch_update_audit_records(updates)

        return outcomes

    def audit_pending(self, limit: Optional[int] = None, batch_size: int = 50, dry_run: bool = False):
        """Executa auditoria em massa de todas as linhas pendentes."""
        self.auth.login()
        return self.audit_service.audit_all(limit=limit, batch_size=batch_size, update_sheet=not dry_run)

    def revalidate_inconsistent(self, batch_size: int = 50, dry_run: bool = False):
        """Revalida as linhas inconsistentes e grava a mensagem detalhada do erro na folha."""
        self.auth.login()
        return self.audit_service.revalidate_inconsistencies(batch_size=batch_size, update_sheet=not dry_run)

    def harmonize_sequentials_and_duplicates(self, dry_run: bool = False):
        """Pré-validação e harmonização de sequenciais Nxxx e remoção de DOCs SOMA duplicados na folha."""
        return self.sheets.harmonize_sequentials_and_duplicates(update_sheet=not dry_run)



