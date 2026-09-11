from __future__ import annotations

import argparse
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--row", type=int, required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--description")
    args = parser.parse_args()
    sheets = GoogleSheetsService(Settings.from_env())
    update = {
        "row_idx": args.row,
        "new_doc": args.doc,
        "auditoria": "Criado no SOMA; pendente revalidação do lote",
    }
    if args.description:
        update["new_desc"] = (
            "DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N002"
            if args.description == "N002"
            else args.description
        )
    sheets.batch_update_audit_records([update])
    print(f"Linha={args.row} DOC={args.doc} atualizado")


if __name__ == "__main__":
    main()
