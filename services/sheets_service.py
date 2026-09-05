from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import gspread
from gspread.utils import ValueRenderOption
from config.settings import Settings
from domain.models import ContaOrdemRow, TipoMovimento

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

    def get_all_rows(self) -> List[ContaOrdemRow]:
        for i in range(5):
            try:
                records = self._ws.get_all_records(
                    numericise_ignore=["all"],
                    value_render_option=ValueRenderOption.formatted,
                )
                out = []
                for idx, r in enumerate(records, start=2):
                    out.append(ContaOrdemRow.from_dict(row_number=idx, raw=r))
                return out
            except Exception as e:
                if "429" in str(e) and i < 4:
                    time.sleep(20)
                else:
                    raise

    def get_auditable_rows(self) -> List[ContaOrdemRow]:
        """Retorna linhas pendentes de auditoria (AUDITORIA vazia e TIPO Entrada ou Saída)."""
        all_rows = self.get_all_rows()
        return [
            r for r in all_rows
            if not r.auditoria.strip() and r.tipo in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA)
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
        header_map = {h.strip(): i + 1 for i, h in enumerate(headers)}
        data_to_batch = []
        for upd in updates_list:
            row_idx = upd["row_idx"]
            cells = [("AUDITORIA", upd["auditoria"])]
            if upd.get("new_doc"):
                cells.append(("DOC. SOMA", upd["new_doc"]))
            if upd.get("new_desc"):
                cells.append(("DESCRIÇÃO SOMA", upd["new_desc"]))
            if upd.get("dados_doc"):
                cells.append(("DADOS DOC", upd["dados_doc"]))
            if upd.get("status"):
                cells.append(("STATUS", upd["status"]))

            for col_name, val in cells:
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
                        time.sleep(20)
                    else:
                        raise

