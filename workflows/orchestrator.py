from __future__ import annotations

import logging
import time
from typing import List, Optional
from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import AuditOutcome, ContaOrdemRow, OperationOutcome, TipoMovimento
from services.audit_service import AuditService
from services.duplicate_checker import DuplicateChecker
from services.sheets_service import GoogleSheetsService
from services.soma_api_service import SomaApiService

logger = logging.getLogger("soma_direct.orchestrator")


class DirectOrchestrator:
    """Orquestrador modular e rápido para o SOMA (Backend HTTP Direto)."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings.from_env()
        self.http = ResilientSession(timeout=self.settings.timeout_seconds)
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

        # 1. Pré-checagem de duplicidade no SOMA
        existing_doc = self.duplicate_checker.check_exists(row)
        if existing_doc:
            logger.info(f"-> Registro já lançado anteriormente no SOMA com DOC {existing_doc}. Atualizando planilha...")
            self.sheets.mark_row_completed(
                row_idx=row.row_number,
                doc_id=existing_doc,
                dados_doc=f"Documento recuperado do SOMA ({existing_doc})",
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

        # 2. Execução da operação correspondente
        if row.tipo == TipoMovimento.SAIDA:
            outcome = self.api.criar_saida(row)
        elif row.tipo == TipoMovimento.ENTRADA:
            outcome = self.api.criar_entrada(row)
        else:
            outcome = self.api.criar_transferencia(row)

        # 3. Atualização na planilha Google Sheets
        if outcome.success:
            logger.info(f"-> SUCESSO Linha {row.row_number}: DOC. SOMA={outcome.doc_id} em {outcome.elapsed_ms}ms")
            self.sheets.mark_row_completed(
                row_idx=row.row_number,
                doc_id=outcome.doc_id,
                dados_doc=outcome.dados_doc,
                elapsed_ms=outcome.elapsed_ms
            )
        else:
            logger.error(f"-> ERRO Linha {row.row_number}: {outcome.error_message}")

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
            outcomes.append(self.process_row(row, dry_run=dry_run))

        total_ms = int((time.perf_counter() - overall_t0) * 1000)
        logger.info(f"=== BATCH FINALIZADO: {len(outcomes)} linhas processadas em {total_ms/1000:.2f}s ({total_ms}ms) ===")
        return outcomes

    def run_pending(self, limit: Optional[int] = None, dry_run: bool = False) -> List[OperationOutcome]:
        """Varre a planilha CONTAORDEM e processa todos os registros pendentes."""
        self.initialize()
        logger.info("Buscando registros pendentes na planilha...")
        all_rows = self.sheets.get_all_rows()

        pending = []
        for r in all_rows:
            doc = (r.doc_soma or "").strip().upper()
            status = (r.status or "").strip().upper()
            if not doc or doc == "TESTE" or doc.startswith("SEM_DOC") or status not in ("VALIDADO", "OK"):
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

    def audit_pending(self, limit: Optional[int] = None, batch_size: int = 25, dry_run: bool = False):
        """Executa auditoria em massa de todas as linhas pendentes."""
        self.auth.login()
        return self.audit_service.audit_all(limit=limit, batch_size=batch_size, update_sheet=not dry_run)

