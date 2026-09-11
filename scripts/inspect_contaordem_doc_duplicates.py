from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from domain.models import norm_basic, normalize_document_value
from services.sheets_service import GoogleSheetsService


def main() -> None:
    sheets = GoogleSheetsService(Settings.from_env())
    values = sheets._ws.get_all_values()
    indices = {norm_basic(header): index for index, header in enumerate(values[0])}

    def value(row, field):
        index = indices.get(norm_basic(field))
        return row[index].strip() if index is not None and index < len(row) else ""

    rows_by_doc = defaultdict(list)
    for row_number, row in enumerate(values[1:], start=2):
        doc = normalize_document_value(value(row, "DOC. SOMA"))
        if doc:
            rows_by_doc[doc].append((row_number, row))

    numeric_duplicates = {
        doc: rows for doc, rows in rows_by_doc.items()
        if doc.isdigit() and len(rows) > 1
    }
    marker_duplicates = {
        doc: rows for doc, rows in rows_by_doc.items()
        if not doc.isdigit() and len(rows) > 1
    }
    print(json.dumps({
        "numeric_duplicate_docs": len(numeric_duplicates),
        "numeric_duplicate_rows": sum(len(rows) for rows in numeric_duplicates.values()),
        "repeated_markers": {doc: len(rows) for doc, rows in marker_duplicates.items()},
    }, ensure_ascii=False, indent=2))

    for doc, rows in numeric_duplicates.items():
        print(f"\nDOC_SOMA={doc} ocorrencias={len(rows)}")
        for row_number, row in rows:
            print(json.dumps({
                "linha": row_number,
                "id_interno": value(row, "ID_INTERNO"),
                "processo": value(row, "PROCESSO"),
                "data": value(row, "DATA MOV."),
                "tipo": value(row, "TIPO"),
                "descricao": value(row, "DESCRIÇÃO"),
                "valor": value(row, "IMPORTÂNCIA"),
                "status": value(row, "STATUS"),
                "auditoria": value(row, "AUDITORIA"),
            }, ensure_ascii=False))


if __name__ == "__main__":
    main()
