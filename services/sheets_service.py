from __future__ import annotations

import logging
import re
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import gspread
from gspread.utils import ValueRenderOption

from config.settings import Settings
from domain.models import (
    ContaOrdemRow,
    TipoMovimento,
    is_entrada_ou_saida,
    norm_basic,
    strip_date_prefix,
    strip_suffix_n,
)

logger = logging.getLogger("soma_direct.sheets")


class GoogleSheetsService:
    """Cliente Google Sheets resiliente com batch update e retry para cota 429."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._gc = gspread.service_account(filename=settings.google_credentials_path)
        self._sh = self._open_with_retry(settings.spreadsheet_url)
        self._ws = self._sh.worksheet(settings.sheet_contaordem)
        self._headers_cache: Optional[List[str]] = None

    def _open_with_retry(self, url: str, max_retries: int = 5) -> gspread.Spreadsheet:
        for i in range(max_retries):
            try:
                return self._gc.open_by_url(url)
            except Exception as e:
                if "429" in str(e) and i < max_retries - 1:
                    logger.warning("Cota da Google Sheets excedida. Aguardando 20 segundos...")
                    time.sleep(20)
                else:
                    raise

    def get_headers(self) -> List[str]:
        if self._headers_cache is None:
            self._headers_cache = [str(h).strip() for h in self._ws.row_values(1)]
        return self._headers_cache

    @staticmethod
    def _col_letter(col_idx: int) -> str:
        res = ""
        while col_idx > 0:
            col_idx, remainder = divmod(col_idx - 1, 26)
            res = chr(65 + remainder) + res
        return res

    def get_all_rows(self, only_entrada_saida: bool = True) -> List[ContaOrdemRow]:
        """Retorna linhas da planilha CONTAORDEM.
        
        Por regra, lê apenas registros com TIPO igual a 'Entrada' ou 'Saída'.
        """
        for i in range(5):
            try:
                records = self._ws.get_all_records(
                    numericise_ignore=["all"],
                    value_render_option=ValueRenderOption.formatted,
                )
                out = []
                for idx, r in enumerate(records, start=2):
                    row = ContaOrdemRow.from_dict(row_number=idx, raw=r)
                    if only_entrada_saida and not is_entrada_ou_saida(row.tipo):
                        continue
                    out.append(row)
                return out
            except Exception as e:
                if "429" in str(e) and i < 4:
                    time.sleep(20)
                else:
                    raise

    def get_auditable_rows(self) -> List[ContaOrdemRow]:
        """Retorna linhas pendentes de auditoria (AUDITORIA vazia e TIPO Entrada ou Saída)."""
        all_rows = self.get_all_rows(only_entrada_saida=True)
        return [
            r for r in all_rows
            if not r.auditoria.strip() and is_entrada_ou_saida(r.tipo)
        ]

    def get_row(self, row_idx: int) -> Optional[ContaOrdemRow]:
        for i in range(5):
            try:
                headers = self.get_headers()
                vals = self._ws.row_values(row_idx, value_render_option=ValueRenderOption.formatted)
                if not vals:
                    return None
                while len(vals) < len(headers):
                    vals.append("")
                raw_dict = dict(zip(headers, vals))
                return ContaOrdemRow.from_dict(row_number=row_idx, raw=raw_dict)
            except Exception as e:
                if "429" in str(e) and i < 4:
                    time.sleep(15)
                else:
                    raise

    def mark_row_completed(self, row_idx: int, doc_id: str, dados_doc: str = "", elapsed_ms: int = 0) -> None:
        """Atualiza a linha com DOC. SOMA, STATUS, TIMESTAMP e DADOS DOC."""
        headers = self.get_headers()
        header_map = {h.strip(): i + 1 for i, h in enumerate(headers)}
        
        now_str = time.strftime("%d/%m/%Y %H:%M:%S")
        cells_to_update = [
            ("DOC. SOMA", doc_id),
            ("STATUS", "VALIDADO"),
            ("IDUSER", self.settings.user_job_id),
            ("TIMESTAMP", now_str),
        ]
        if dados_doc:
            cells_to_update.append(("DADOS DOC", dados_doc))

        data_to_batch = []
        for col_name, val in cells_to_update:
            if col_name in header_map:
                c_idx = header_map[col_name]
                a1 = f"{self._col_letter(c_idx)}{row_idx}"
                data_to_batch.append({"range": a1, "values": [[val]]})

        if data_to_batch:
            for attempt in range(5):
                try:
                    self._ws.batch_update(data_to_batch)
                    logger.info(f"Linha {row_idx} atualizada na sheet com DOC {doc_id}.")
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 4:
                        time.sleep(15)
                    else:
                        raise

    def claim_row(self, row_idx: int) -> Optional[str]:
        """Tenta reservar uma linha e confirma que este processo venceu a disputa."""
        headers = self.get_headers()
        header_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}
        status_col = header_map.get(norm_basic("STATUS"))
        if not status_col:
            raise RuntimeError("Coluna STATUS não encontrada na CONTAORDEM")
        token = f"EM PROCESSAMENTO:{int(time.time())}:{uuid.uuid4().hex}"
        cell = f"{self._col_letter(status_col)}{row_idx}"
        self._ws.update(cell, [[token]])
        current = self._ws.acell(cell).value or ""
        return token if current == token else None

    def mark_row_failed(self, row_idx: int, message: str) -> None:
        """Liberta a reserva e registra uma falha sem inventar DOC. SOMA."""
        headers = self.get_headers()
        header_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}
        updates = []
        for name, value in (("STATUS", "EM ERRO"), ("DADOS DOC", str(message)[:450])):
            col = header_map.get(norm_basic(name))
            if col:
                updates.append({"range": f"{self._col_letter(col)}{row_idx}", "values": [[value]]})
        if updates:
            self._ws.batch_update(updates)

    def mark_row_validation_error(self, row_idx: int, message: str) -> None:
        """Marca uma linha inválida para análise humana, sem acessar o SOMA."""
        headers = self.get_headers()
        header_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}
        updates = []
        for name, value in (
            ("DOC. SOMA", "Analisar"),
            ("STATUS", "ERRO"),
            ("DADOS DOC", str(message)[:450]),
        ):
            col = header_map.get(norm_basic(name))
            if col:
                updates.append({"range": f"{self._col_letter(col)}{row_idx}", "values": [[value]]})
        if updates:
            self._ws.batch_update(updates)

    def mark_row_audit(
        self,
        row_idx: int,
        auditoria: str,
        new_doc: Optional[str] = None,
        new_desc: Optional[str] = None,
        dados_doc: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        """Atualiza os campos de auditoria de uma linha individualmente."""
        headers = self.get_headers()
        header_map = {h.strip(): i + 1 for i, h in enumerate(headers)}

        cells_to_update = [("AUDITORIA", auditoria)]
        if new_doc is not None:
            cells_to_update.append(("DOC. SOMA", new_doc))
        if new_desc is not None:
            cells_to_update.append(("DESCRIÇÃO SOMA", new_desc))
        if dados_doc is not None:
            cells_to_update.append(("DADOS DOC", dados_doc))
        if status is not None:
            cells_to_update.append(("STATUS", status))

        data_to_batch = []
        for col_name, val in cells_to_update:
            if col_name in header_map:
                c_idx = header_map[col_name]
                a1 = f"{self._col_letter(c_idx)}{row_idx}"
                data_to_batch.append({"range": a1, "values": [[val]]})

        if data_to_batch:
            for attempt in range(5):
                try:
                    self._ws.batch_update(data_to_batch)
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 4:
                        time.sleep(15)
                    else:
                        raise

    def batch_update_audit_records(self, updates_list: List[Dict[str, Any]]) -> None:
        """Aplica múltiplas auditorias de uma só vez para máxima velocidade."""
        if not updates_list:
            return
        headers = self.get_headers()
        norm_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}
        data_to_batch = []
        for upd in updates_list:
            row_idx = upd["row_idx"]
            cells = []
            if upd.get("auditoria") is not None:
                cells.append(("AUDITORIA", upd["auditoria"]))
            if upd.get("new_doc") is not None:
                cells.append(("DOC. SOMA", upd["new_doc"]))
            if upd.get("new_desc") is not None:
                cells.append(("DESCRIÇÃO SOMA", upd["new_desc"]))
            if upd.get("new_tipo") is not None:
                cells.append(("TIPO", upd["new_tipo"]))
            if upd.get("new_data") is not None:
                cells.append(("DATA MOV.", upd["new_data"]))
            if upd.get("dados_doc") is not None:
                cells.append(("DADOS DOC", upd["dados_doc"]))
            if upd.get("status") is not None:
                cells.append(("STATUS", upd["status"]))
            if upd.get("new_caixa") is not None:
                cells.append(("CAIXA", upd["new_caixa"]))
            if upd.get("new_forma_pagamento") is not None:
                cells.append(("FORMA DE PAGAMENTO", upd["new_forma_pagamento"]))

            for col_name, val in cells:
                col_norm = norm_basic(col_name)
                if col_norm in norm_map:
                    c_idx = norm_map[col_norm]
                    a1 = f"{self._col_letter(c_idx)}{row_idx}"
                    data_to_batch.append({"range": a1, "values": [[val]]})

        if data_to_batch:
            chunk_size = 500
            for i in range(0, len(data_to_batch), chunk_size):
                chunk = data_to_batch[i : i + chunk_size]
                for attempt in range(5):
                    try:
                        self._ws.batch_update(chunk)
                        break
                    except Exception as e:
                        if "429" in str(e) and attempt < 4:
                            time.sleep(20)
                        else:
                            raise

    def harmonize_sequentials_and_duplicates(self, update_sheet: bool = True) -> Dict[str, Any]:
        """Pré-validação e harmonização de sequenciais Nxxx e DOC. SOMA duplicados na planilha CONTAORDEM:
        1. Lê todas as linhas da planilha.
        2. Agrupa lançamentos por lote de data (DATA MOV.), tipo (Entrada/Saída) e descrição base (sem data e sem Nxxx).
        3. Para lotes com sequenciais duplicados, fora de ordem ou DOCs SOMA duplicados:
           - Re-sequencia a DESCRIÇÃO SOMA na ordem das linhas: f"{base} N{i:03d}" para i=1..len(items).
           - Limpa o DOC. SOMA, AUDITORIA e STATUS nas linhas subsequentes onde o DOC numérico foi duplicado.
        4. Se update_sheet=True, grava as alterações na planilha Google Sheets via batch_update.
        5. Retorna estatísticas e lista de linhas ajustadas.
        """
        headers = self.get_headers()
        norm_map = {norm_basic(h): i for i, h in enumerate(headers)}

        dt_idx = norm_map.get(norm_basic("DATA MOV."), 0)
        tipo_idx = norm_map.get(norm_basic("TIPO"), 6)
        doc_idx = norm_map.get(norm_basic("DOC. SOMA"), 4)
        desc_idx = norm_map.get(norm_basic("DESCRIÇÃO"), 2)
        desc_soma_idx = norm_map.get(norm_basic("DESCRIÇÃO SOMA"), 9)
        val_idx = norm_map.get(norm_basic("IMPORTÂNCIA"), 3)

        all_vals = []
        for attempt in range(5):
            try:
                all_vals = self._ws.get_all_values()
                break
            except Exception as e:
                if "429" in str(e) and attempt < 4:
                    logger.warning("Cota excedida ao ler planilha. Aguardando 20s...")
                    time.sleep(20)
                else:
                    raise

        data_rows = all_vals[1:]
        groups = defaultdict(list)
        for r_idx, r in enumerate(data_rows, start=2):
            while len(r) < len(headers):
                r.append("")
            t = r[tipo_idx].strip()
            dt = r[dt_idx].strip()
            if not dt or not is_entrada_ou_saida(t):
                continue
            fdesc = r[desc_soma_idx].strip() or r[desc_idx].strip()
            base = strip_date_prefix(strip_suffix_n(fdesc)).strip()
            groups[(dt, norm_basic(t), base.upper())].append((r_idx, r, base))

        updates_list = []
        batches_affected = set()
        total_desc_adjusted = 0
        total_docs_cleared = 0
        adjustments_summary = []

        for (dt, tipo, base_upper), items in groups.items():
            if len(items) <= 1:
                continue

            seqs = [r[desc_soma_idx].strip() for _, r, _ in items]
            docs = [r[doc_idx].strip() for _, r, _ in items]
            has_dupe_seq = len(set(seqs)) < len(seqs)
            has_dupe_doc = len([d for d in docs if d.isdigit()]) > len(set([d for d in docs if d.isdigit()]))
            has_missing_seq = any(not re.search(r"\bN\d{3}\b", s) for s in seqs)

            if has_dupe_seq or has_dupe_doc or has_missing_seq:
                batches_affected.add(dt)
                base_clean = items[0][2]
                seen_docs = set()

                for i, (r_idx, r, _) in enumerate(items, start=1):
                    expected_desc = f"{base_clean} N{i:03d}"
                    cur_desc = r[desc_soma_idx].strip()
                    cur_doc = r[doc_idx].strip()

                    row_upd = {"row_idx": r_idx}
                    changed = False

                    if cur_desc != expected_desc:
                        row_upd["new_desc"] = expected_desc
                        total_desc_adjusted += 1
                        changed = True

                    if cur_doc.isdigit():
                        if cur_doc in seen_docs:
                            # DOC duplicado neste grupo! Limpa DOC. SOMA, STATUS, AUDITORIA e DADOS DOC
                            row_upd["new_doc"] = ""
                            row_upd["status"] = ""
                            row_upd["auditoria"] = ""
                            row_upd["dados_doc"] = ""
                            total_docs_cleared += 1
                            changed = True
                            adjustments_summary.append({
                                "row": r_idx,
                                "data": dt,
                                "valor": r[val_idx],
                                "desc_antiga": cur_desc,
                                "desc_nova": expected_desc,
                                "doc_limpo": cur_doc,
                            })
                        else:
                            seen_docs.add(cur_doc)

                    if changed:
                        updates_list.append(row_upd)

        logger.info(
            f"Harmonização pré-validação: {len(batches_affected)} lotes de data afetados, "
            f"{total_desc_adjusted} descrições ajustadas, {total_docs_cleared} DOCs duplicados limpos."
        )

        if update_sheet and updates_list:
            self.batch_update_audit_records(updates_list)
            logger.info("Planilha CONTAORDEM atualizada com sucesso após harmonização.")

        return {
            "batches_affected": len(batches_affected),
            "total_desc_adjusted": total_desc_adjusted,
            "total_docs_cleared": total_docs_cleared,
            "updates_count": len(updates_list),
            "adjustments_summary": adjustments_summary,
        }


