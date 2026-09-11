from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService


def main() -> None:
    sheets = GoogleSheetsService(Settings.from_env())
    rows = sheets.get_all_rows(only_entrada_saida=True)
    updates = [
        {"row_idx": row.row_number, "auditoria": "Perfeito"}
        for row in rows
        if row.auditoria == "Criado no SOMA; pendente revalidação do lote"
    ]
    if updates:
        sheets.batch_update_audit_records(updates)
    print(f"Linhas finalizadas={len(updates)}")


if __name__ == "__main__":
    main()
