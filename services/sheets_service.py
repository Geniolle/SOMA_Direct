from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import gspread
from config.settings import Settings
from domain.models import ContaOrdemRow

logger = logging.getLogger("soma_direct.sheets")


class GoogleSheetsService:
    """Cliente Google Sheets resiliente com batch update e retry para cota 429."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._gc = gspread.service_account(filename=settings.google_credentials_path)
        self._sh = self._open_with_retry(settings.spreadsheet_url)
        self._ws = self._sh.worksheet(settings.sheet_contaordem)

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

    def get_all_rows(self) -> List[ContaOrdemRow]:
        for i in range(5):
            try:
                records = self._ws.get_all_records()
                out = []
                for idx, r in enumerate(records, start=2):
                    out.append(ContaOrdemRow.from_dict(row_number=idx, raw=r))
                return out
            except Exception as e:
                if "429" in str(e) and i < 4:
                    time.sleep(20)
                else:
                    raise

    def get_row(self, row_idx: int) -> Optional[ContaOrdemRow]:
        for i in range(5):
            try:
                headers = self._ws.row_values(1)
                vals = self._ws.row_values(row_idx)
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
        # Mapeamento de colunas da linha 1
        headers = self._ws.row_values(1)
        header_map = {h.strip(): i + 1 for i, h in enumerate(headers)}
        
        updates = []
        now_str = time.strftime("%d/%m/%Y %H:%M:%S")

        def col_letter(col_idx: int) -> str:
            res = ""
            while col_idx > 0:
                col_idx, remainder = divmod(col_idx - 1, 26)
                res = chr(65 + remainder) + res
            return res

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
                a1 = f"{col_letter(c_idx)}{row_idx}"
                data_to_batch.append({"range": a1, "values": [[val]]})

        if data_to_batch:
            self._ws.batch_update(data_to_batch)
            logger.info(f"Linha {row_idx} atualizada na sheet com DOC {doc_id}.")
