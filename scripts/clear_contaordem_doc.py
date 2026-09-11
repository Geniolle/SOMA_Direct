from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from domain.models import norm_basic
from services.sheets_service import GoogleSheetsService


def main() -> None:
    row_number = int(sys.argv[1])
    sheets = GoogleSheetsService(Settings.from_env())
    headers = sheets.get_headers()
    document_column = next(
        index for index, header in enumerate(headers, start=1)
        if norm_basic(header) == norm_basic("DOC. SOMA")
    )
    sheets._ws.update_cell(row_number, document_column, "")
    print(f"DOC. SOMA limpo na linha {row_number}")


if __name__ == "__main__":
    main()
