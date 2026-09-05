from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from config.settings import Settings
from core.http_session import ResilientSession
from domain.models import (
    AuditOutcome,
    ContaOrdemRow,
    SomaSearchResult,
    TipoMovimento,
    clean_amount_for_comparison,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
    strip_suffix_n,
    validate_dados_doc,
)
from services.sheets_service import GoogleSheetsService

logger = logging.getLogger("soma_direct.audit")


class AuditService:
    """Serviço de alta performance para conciliação e auditoria de lançamentos SOMA via HTTP direto."""

    def __init__(self, settings: Optional[Settings] = None, http: Optional[ResilientSession] = None, sheets: Optional[GoogleSheetsService] = None):
        self.settings = settings
        self.http = http
        self.sheets = sheets
        self.base_url = (settings.site_base_url.rstrip("/") + "/") if settings else ""

    def _parse_search_table(self, html: str) -> List[SomaSearchResult]:
        results: List[SomaSearchResult] = []
        for rw in re.findall(r'<tr\b[^>]*>(.*?)</tr>', html, re.DOTALL):
            cells = [
                re.sub(r'<[^>]+>', ' ', c).strip()
                for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', rw, re.DOTALL)
            ]
            if len(cells) >= 9:
                codigo = cells[2]
                if codigo.lower() == "codigo":
                    continue
                results.append(
                    SomaSearchResult(
                        codigo=codigo,
                        tipo=cells[3],
                        descricao=cells[4],
                        valor=cells[5],
                        data=cells[6],
                        status=cells[7],
                        baixa=cells[8],
                    )
                )
        return results

    def search_by_codigo(self, doc_soma: str) -> Optional[SomaSearchResult]:
        doc_clean = normalize_document_value(doc_soma)
        if not doc_clean:
            return None

        payload = {
            "pesquisa": doc_clean,
            "filtro": "codigo",
            "id_inst": self.settings.institution_id,
            "tipo": "2",
            "v": "0",
            "s": "2",
            "t_d": "1",
            "cc": "-1",
            "c": "",
        }
        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=payload)
        items = self._parse_search_table(resp.text)
        for item in items:
            if normalize_document_value(item.codigo) == doc_clean:
                return item
        return items[0] if items else None

    def search_by_descricao(self, descricao: str, data_mov: str = "") -> List[SomaSearchResult]:
        desc_clean = str(descricao or "").strip()
        if not desc_clean:
            return []

        payload = {
            "pesquisa": desc_clean,
            "filtro": "descricao",
            "id_inst": self.settings.institution_id,
            "tipo": "2",
            "v": "0",
            "s": "2",
            "t_d": "1",
            "cc": "-1",
            "c": "",
        }
        if data_mov:
            d_norm = normalize_date_str(data_mov)
            payload["i"] = d_norm
            payload["f"] = d_norm

        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=payload)
        return self._parse_search_table(resp.text)

    def search_by_periodo(self, data_mov: str) -> List[SomaSearchResult]:
        d_norm = normalize_date_str(data_mov)
        if not d_norm:
            return []

        payload = {
            "pesquisa": "",
            "filtro": "descricao",
            "id_inst": self.settings.institution_id,
            "tipo": "2",
            "v": "0",
            "s": "2",
            "t_d": "1",
            "cc": "-1",
            "c": "",
            "i": d_norm,
            "f": d_norm,
        }
        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=payload)
        return self._parse_search_table(resp.text)

    def fetch_dados_doc(self, doc_id: str) -> str:
        doc_clean = normalize_document_value(doc_id)
        if not doc_clean:
            return ""

        url = f"{self.base_url}?mod=ivv&exec=entradas_saidas_dados&ID={doc_clean}"
        try:
            resp = self.http.get(url)
            rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', resp.text, re.DOTALL)
            for r in rows:
                tds = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<td\b[^>]*>(.*?)</td>', r, re.DOTALL)]
                if len(tds) >= 4 and "Registrado" in tds[3]:
                    return tds[3]
            for r in rows:
                tds = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<td\b[^>]*>(.*?)</td>', r, re.DOTALL)]
                if len(tds) >= 4 and ("Baixa realizada" in tds[3] or "Caixa" in tds[3]):
                    return tds[3]
            return ""
        except Exception as e:
            logger.warning(f"Falha ao buscar DADOS DOC para {doc_clean}: {e}")
            return ""

    def validate_soma_record(
        self,
        row: ContaOrdemRow,
        result: Optional[SomaSearchResult],
    ) -> Tuple[str, List[str]]:
        """
        Valida as colunas do site SOMA contra os valores da folha:
        - Coluna 3: Código vs DOC. SOMA
        - Coluna 4: Tipo vs TIPO
        - Coluna 5: Descrição vs DESCRIÇÃO SOMA
        - Coluna 6: Valor vs IMPORTÂNCIA
        - Coluna 7: Vencimento / Entrada vs DATA MOV.
        - Coluna 8: Status (fixo esperado: PAGO)
        - Coluna 9: Baixa (fixo esperado: SIM)
        """
        if result is None:
            return "Inconsistente", ["Registo não encontrado no portal SOMA"]

        inconsistencias: List[str] = []

        doc_esperado = normalize_document_value(row.doc_soma)
        doc_site = normalize_document_value(result.codigo)
        if not doc_esperado or doc_esperado != doc_site:
            inconsistencias.append(f"CÓDIGO (col 3): site='{doc_site}' != sheet='{doc_esperado}'")

        tipo_esperado = norm_basic(row.tipo.value)
        tipo_site = norm_basic(result.tipo)
        if tipo_esperado != tipo_site:
            inconsistencias.append(f"TIPO (col 4): site='{result.tipo}' != sheet='{row.tipo.value}'")

        desc_esperada = norm_basic(row.descricao_soma or row.descricao)
        desc_site = norm_basic(result.descricao)
        if desc_esperada != desc_site:
            inconsistencias.append(f"DESCRIÇÃO (col 5): site='{result.descricao}' != sheet='{desc_esperada}'")

        valor_esperado = clean_amount_for_comparison(row.importancia)
        valor_site = clean_amount_for_comparison(result.valor)
        if valor_esperado != valor_site:
            inconsistencias.append(f"VALOR (col 6): site='{valor_site}' != sheet='{valor_esperado}'")

        data_esperada = normalize_date_str(row.data_mov)
        data_site = normalize_date_str(result.data)
        if data_esperada != data_site:
            inconsistencias.append(f"DATA (col 7): site='{data_site}' != sheet='{data_esperada}'")

        if norm_basic(result.status) != "pago":
            inconsistencias.append(f"STATUS (col 8): site='{result.status}' != esperado='PAGO'")

        if norm_basic(result.baixa) != "sim":
            inconsistencias.append(f"BAIXA (col 9): site='{result.baixa}' != esperado='SIM'")

        if inconsistencias:
            return "Inconsistente", inconsistencias

        return "Confirmado", []

    def matches_ignoring_code_and_suffix(
        self,
        row: ContaOrdemRow,
        result: Optional[SomaSearchResult],
    ) -> Tuple[bool, bool]:
        """
        Verifica se o registro bate nos campos essenciais:
        - Tipo, Valor, Data, Status==PAGO, Baixa==SIM
        - Descrição base (sem sufixo Nxxx)
        Retorna (matches: bool, desc_differs: bool)
        """
        if result is None:
            return False, False

        if norm_basic(row.tipo.value) != norm_basic(result.tipo):
            return False, False

        if clean_amount_for_comparison(row.importancia) != clean_amount_for_comparison(result.valor):
            return False, False

        if normalize_date_str(row.data_mov) != normalize_date_str(result.data):
            return False, False

        if norm_basic(result.status) != "pago":
            return False, False

        if norm_basic(result.baixa) != "sim":
            return False, False

        base_esperada = norm_basic(strip_suffix_n(row.descricao_soma or row.descricao))
        base_site = norm_basic(strip_suffix_n(result.descricao))
        if base_esperada != base_site:
            return False, False

        desc_differs = norm_basic(row.descricao_soma or row.descricao) != norm_basic(result.descricao)
        return True, desc_differs

    def _process_dados_doc_and_finalize(
        self,
        row: ContaOrdemRow,
        matched_doc: str,
        new_desc: Optional[str] = None,
        is_correction: bool = False,
    ) -> AuditOutcome:
        sheet_dados = str(row.dados_doc or "").strip()
        dados_doc_site = ""

        # Se já temos DADOS DOC na sheet e ele valida com Caixa e Forma, usamos diretamente (hiper-rápido)
        dados_valido = False
        dados_err = None
        if sheet_dados and not is_correction:
            dados_valido, dados_err = validate_dados_doc(
                dados_doc=sheet_dados,
                sheet_caixa=row.caixa,
                sheet_forma=row.forma_pagamento,
            )

        # Se não temos ou a validação local falhou/era correção, consulta a página de detalhes no portal
        if not dados_valido:
            dados_doc_site = self.fetch_dados_doc(matched_doc)
            dados_valido, dados_err = validate_dados_doc(
                dados_doc=dados_doc_site or sheet_dados,
                sheet_caixa=row.caixa,
                sheet_forma=row.forma_pagamento,
            )

        if not dados_valido:
            logger.warning(f"Linha {row.row_number}: Falha Caixa/Forma em DADOS DOC ({dados_err})")
            return AuditOutcome(
                analyzed=True,
                inconsistent=True,
                inconsistencies=[dados_err or "DADOS DOC inválido"],
                dados_doc=dados_doc_site or sheet_dados,
            )

        if is_correction:
            logger.info(
                f"Linha {row.row_number}: Corrigido! DOC '{row.doc_soma}' -> '{matched_doc}', "
                f"DESC '{row.descricao_soma}' -> '{new_desc or row.descricao_soma}'"
            )
            return AuditOutcome(
                analyzed=True,
                corrected=True,
                new_doc=matched_doc,
                new_desc=new_desc,
                dados_doc=dados_doc_site or sheet_dados,
            )

        logger.info(f"Linha {row.row_number}: Confirmado com sucesso! (DOC {matched_doc})")
        return AuditOutcome(
            analyzed=True,
            confirmed=True,
            dados_doc=dados_doc_site or sheet_dados,
        )


    def audit_row(self, row: ContaOrdemRow) -> AuditOutcome:
        """Audita uma única linha seguindo a cascata de 3 etapas com validação de DADOS DOC."""
        doc_soma = normalize_document_value(row.doc_soma)

        # ETAPA 1: Pesquisa por Código Documento
        if doc_soma:
            search_result = self.search_by_codigo(doc_soma)
            auditoria, inconsistencias = self.validate_soma_record(row, search_result)
            if auditoria == "Confirmado" and search_result is not None:
                return self._process_dados_doc_and_finalize(
                    row=row,
                    matched_doc=normalize_document_value(search_result.codigo),
                )
        else:
            inconsistencias = ["DOC. SOMA não preenchido"]

        # ETAPA 2: Fallback por Descrição + Período
        desc_candidates = self.search_by_descricao(row.descricao_soma or row.descricao, data_mov=row.data_mov)
        if desc_candidates:
            for cand in desc_candidates:
                cand_audit, _ = self.validate_soma_record(row, cand)
                if cand_audit == "Confirmado":
                    return self._process_dados_doc_and_finalize(
                        row=row,
                        matched_doc=normalize_document_value(cand.codigo),
                    )
                matched, desc_differs = self.matches_ignoring_code_and_suffix(row, cand)
                if matched:
                    return self._process_dados_doc_and_finalize(
                        row=row,
                        matched_doc=normalize_document_value(cand.codigo),
                        new_desc=cand.descricao if desc_differs else None,
                        is_correction=True,
                    )

        # ETAPA 3: Fallback por Período (sem texto no filtro de busca)
        periodo_candidates = self.search_by_periodo(row.data_mov)
        if periodo_candidates:
            # Prioriza match exato de descrição completa
            target_desc = norm_basic(row.descricao_soma or row.descricao)
            for cand in periodo_candidates:
                matched, desc_differs = self.matches_ignoring_code_and_suffix(row, cand)
                if matched and norm_basic(cand.descricao) == target_desc:
                    return self._process_dados_doc_and_finalize(
                        row=row,
                        matched_doc=normalize_document_value(cand.codigo),
                        new_desc=cand.descricao if desc_differs else None,
                        is_correction=True,
                    )
            # Match ignorando sufixo Nxxx
            for cand in periodo_candidates:
                matched, desc_differs = self.matches_ignoring_code_and_suffix(row, cand)
                if matched:
                    return self._process_dados_doc_and_finalize(
                        row=row,
                        matched_doc=normalize_document_value(cand.codigo),
                        new_desc=cand.descricao if desc_differs else None,
                        is_correction=True,
                    )

        return AuditOutcome(
            analyzed=True,
            inconsistent=True,
            inconsistencies=inconsistencias or ["Registo não localizado no portal SOMA"],
        )

    def audit_all(
        self,
        limit: Optional[int] = None,
        batch_size: int = 25,
        update_sheet: bool = True,
    ) -> Dict[str, Any]:
        """Executa auditoria em lote com atualização periódica e métricas de performance."""
        t0 = time.perf_counter()
        logger.info("Carregando linhas pendentes de auditoria da planilha...")
        rows = self.sheets.get_auditable_rows()

        if limit and limit > 0:
            rows = rows[:limit]

        total_rows = len(rows)
        logger.info(f"Total de linhas a auditar: {total_rows}")
        print(f"\n[AUDITORIA SOMA] Total de linhas selecionadas: {total_rows}\n", flush=True)

        stats = {
            "total": total_rows,
            "confirmed": 0,
            "corrected": 0,
            "inconsistent": 0,
            "errors": 0,
        }

        updates_buffer: List[Dict[str, Any]] = []

        for idx, row in enumerate(rows, start=1):
            t_row = time.perf_counter()
            try:
                outcome = self.audit_row(row)
                if outcome.confirmed:
                    stats["confirmed"] += 1
                    status_str = None
                    aud_str = "Confirmado"
                elif outcome.corrected:
                    stats["corrected"] += 1
                    status_str = None
                    aud_str = "Corrigido"
                else:
                    stats["inconsistent"] += 1
                    status_str = "ERRO" if "DADOS DOC" in "; ".join(outcome.inconsistencies) else None
                    aud_str = "Inconsistente"

                updates_buffer.append({
                    "row_idx": row.row_number,
                    "auditoria": aud_str,
                    "new_doc": outcome.new_doc,
                    "new_desc": outcome.new_desc,
                    "dados_doc": outcome.dados_doc if outcome.dados_doc != row.dados_doc else None,
                    "status": status_str,
                })

                elapsed_ms = int((time.perf_counter() - t_row) * 1000)
                logger.debug(f"Linha {row.row_number} auditada em {elapsed_ms}ms -> {aud_str}")

            except Exception as e:
                stats["errors"] += 1
                logger.exception(f"Erro na auditoria da linha {row.row_number}: {e}")

            # Batch update a cada batch_size linhas
            if update_sheet and len(updates_buffer) >= batch_size:
                self.sheets.batch_update_audit_records(updates_buffer)
                updates_buffer.clear()

            # Feedback no console
            if idx % 10 == 0 or idx == total_rows:
                pct = (idx / total_rows) * 100 if total_rows > 0 else 100
                print(
                    f"Progresso: {idx}/{total_rows} ({pct:.1f}%) | "
                    f"Confirmados: {stats['confirmed']} | Corrigidos: {stats['corrected']} | "
                    f"Inconsistentes: {stats['inconsistent']} | Erros: {stats['errors']}",
                    flush=True,
                )

        # Atualiza o buffer restante
        if update_sheet and updates_buffer:
            self.sheets.batch_update_audit_records(updates_buffer)
            updates_buffer.clear()

        total_elapsed = time.perf_counter() - t0
        stats["total_elapsed_sec"] = round(total_elapsed, 2)
        stats["avg_per_row_sec"] = round(total_elapsed / total_rows, 3) if total_rows > 0 else 0

        print("\n" + "=" * 70)
        print("RESUMO DA AUDITORIA SOMA_DIRECT")
        print("=" * 70)
        print(f"Total analisado:   {stats['total']}")
        print(f"Confirmados:       {stats['confirmed']}")
        print(f"Corrigidos:        {stats['corrected']}")
        print(f"Inconsistentes:    {stats['inconsistent']}")
        print(f"Erros técnicos:    {stats['errors']}")
        print(f"Tempo total:       {stats['total_elapsed_sec']} segundos")
        print(f"Média por linha:   {stats['avg_per_row_sec']} segundos")
        print("=" * 70 + "\n")

        return stats

    def get_detailed_inconsistency_message(self, row: ContaOrdemRow) -> str:
        """Gera mensagem explicativa e concisa para a coluna AUDITORIA em vez de apenas 'Inconsistente'."""
        doc = normalize_document_value(row.doc_soma)

        # 1. Não numérico ou vazio
        if not doc:
            return "DOC. SOMA vazio na folha"
        if not doc.isdigit():
            return f"DOC não numérico ('{doc}')"

        # 2. Busca por código
        search_res = self.search_by_codigo(doc)
        if search_res is None:
            return f"Código {doc} não existe no SOMA"

        # 3. Validação das 7 colunas
        val_status, incs = self.validate_soma_record(row, search_res)
        if not incs:
            dados_site = self.fetch_dados_doc(doc)
            _, err = validate_dados_doc(dados_site or row.dados_doc, row.caixa, row.forma_pagamento)
            if err:
                return err[:85]
            return "DADOS DOC inválido ou ausente"

        has_val = any("VALOR" in i for i in incs)
        has_desc = any("DESCRIÇÃO" in i for i in incs)
        has_tipo = any("TIPO" in i for i in incs)
        has_data = any("DATA" in i for i in incs)
        has_status = any("STATUS" in i for i in incs)
        has_baixa = any("BAIXA" in i for i in incs)

        if has_status or has_baixa:
            return f"Não quitado no SOMA (Status={search_res.status} / Baixa={search_res.baixa})"

        if has_val and has_desc:
            desc_abbrev = search_res.descricao[:25].strip()
            return f"DOC cruzado (SOMA: {desc_abbrev} | {search_res.valor} €)"

        if has_tipo:
            return f"Tipo divergente: site={search_res.tipo} != sheet={row.tipo.value}"

        if has_val:
            return f"Valor divergente: site={search_res.valor} € != sheet={row.importancia} €"

        if has_desc:
            desc_abbrev = search_res.descricao[:30].strip()
            return f"Descrição divergente: site='{desc_abbrev}'"

        if has_data:
            return f"Data divergente: site={search_res.data} != sheet={row.data_mov}"

        return "; ".join(incs)[:75]

    def revalidate_inconsistencies(
        self,
        batch_size: int = 50,
        update_sheet: bool = True,
    ) -> Dict[str, Any]:
        """Revalida todas as linhas marcadas como 'Inconsistente' e substitui pelo motivo detalhado do erro."""
        t0 = time.perf_counter()
        logger.info("Carregando registros inconsistentes para revalidação detalhada...")

        all_rows = self.sheets.get_all_rows()
        inconsistent_rows = [r for r in all_rows if r.auditoria.strip() == "Inconsistente"]
        total_rows = len(inconsistent_rows)

        print(f"\n[REVALIDAÇÃO DE INCONSISTÊNCIAS] Total de linhas: {total_rows}\n", flush=True)

        stats = {
            "total": total_rows,
            "confirmed_now": 0,
            "corrected_now": 0,
            "detailed_errors": 0,
            "errors": 0,
        }

        updates_buffer: List[Dict[str, Any]] = []

        for idx, row in enumerate(inconsistent_rows, start=1):
            t_row = time.perf_counter()
            try:
                # 1. Tenta auditoria completa primeiro (caso agora passe como Confirmado ou Corrigido)
                outcome = self.audit_row(row)
                if outcome.confirmed:
                    stats["confirmed_now"] += 1
                    aud_text = "Confirmado"
                    new_doc = None
                    new_desc = None
                    status_str = None
                elif outcome.corrected:
                    stats["corrected_now"] += 1
                    aud_text = "Corrigido"
                    new_doc = outcome.new_doc
                    new_desc = outcome.new_desc
                    status_str = None
                else:
                    stats["detailed_errors"] += 1
                    aud_text = self.get_detailed_inconsistency_message(row)
                    new_doc = None
                    new_desc = None
                    status_str = "ERRO" if ("DADOS DOC" in aud_text or "CAIXA" in aud_text or "FORMA" in aud_text) else None

                updates_buffer.append({
                    "row_idx": row.row_number,
                    "auditoria": aud_text,
                    "new_doc": new_doc,
                    "new_desc": new_desc,
                    "dados_doc": outcome.dados_doc if outcome.dados_doc and outcome.dados_doc != row.dados_doc else None,
                    "status": status_str,
                })

                elapsed_ms = int((time.perf_counter() - t_row) * 1000)
                logger.debug(f"Linha {row.row_number} revalidada em {elapsed_ms}ms -> {aud_text}")

            except Exception as e:
                stats["errors"] += 1
                logger.exception(f"Erro na revalidação da linha {row.row_number}: {e}")

            if update_sheet and len(updates_buffer) >= batch_size:
                self.sheets.batch_update_audit_records(updates_buffer)
                updates_buffer.clear()

            if idx % 10 == 0 or idx == total_rows:
                pct = (idx / total_rows) * 100 if total_rows > 0 else 100
                print(
                    f"Progresso: {idx}/{total_rows} ({pct:.1f}%) | "
                    f"Confirmados: {stats['confirmed_now']} | Corrigidos: {stats['corrected_now']} | "
                    f"Erros detalhados: {stats['detailed_errors']}",
                    flush=True,
                )

        if update_sheet and updates_buffer:
            self.sheets.batch_update_audit_records(updates_buffer)
            updates_buffer.clear()

        total_elapsed = time.perf_counter() - t0
        stats["total_elapsed_sec"] = round(total_elapsed, 2)

        print("\n" + "=" * 70)
        print("RESUMO DA REVALIDAÇÃO DE INCONSISTÊNCIAS")
        print("=" * 70)
        print(f"Total revalidado:           {stats['total']}")
        print(f"Passaram para Confirmado:   {stats['confirmed_now']}")
        print(f"Passaram para Corrigido:    {stats['corrected_now']}")
        print(f"Atualizados com Erro Real:  {stats['detailed_errors']}")
        print(f"Tempo total:                {stats['total_elapsed_sec']} segundos")
        print("=" * 70 + "\n")

        return stats

