from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from services.sheets_service import GoogleSheetsService
from config.settings import Settings
from domain.models import norm_basic


FIELDS = (
    "PROCESSO",
    "DATA MOV.",
    "TIPO",
    "DESCRIÇÃO",
    "IMPORTÂNCIA",
    "DOC. SOMA",
    "STATUS",
    "AUDITORIA",
)


def main() -> None:
    sheets = GoogleSheetsService(Settings.from_env())
    values = sheets._ws.get_all_values()
    headers = values[0]
    normalized_headers = {norm_basic(header): index for index, header in enumerate(headers)}
    id_index = normalized_headers[norm_basic("ID_INTERNO")]
    field_indices = {
        field: normalized_headers.get(norm_basic(field))
        for field in FIELDS
    }

    rows_by_id = defaultdict(list)
    for row_number, row in enumerate(values[1:], start=2):
        id_interno = row[id_index].strip() if id_index < len(row) else ""
        if id_interno:
            rows_by_id[id_interno].append((row_number, row))

    duplicates = {
        id_interno: rows
        for id_interno, rows in rows_by_id.items()
        if len(rows) > 1
    }
    print(json.dumps({
        "unique_duplicate_ids": len(duplicates),
        "duplicate_rows": sum(len(rows) for rows in duplicates.values()),
    }, ensure_ascii=False, indent=2))

    for id_interno, rows in duplicates.items():
        print(f"\nID_INTERNO={id_interno} ocorrencias={len(rows)}")
        signatures = set()
        docs = set()
        for row_number, row in rows:
            details = {}
            for field, index in field_indices.items():
                details[field] = row[index].strip() if index is not None and index < len(row) else ""
            signatures.add(tuple(details[field] for field in ("DATA MOV.", "TIPO", "DESCRIÇÃO", "IMPORTÂNCIA")))
            if details["DOC. SOMA"]:
                docs.add(details["DOC. SOMA"])
            print(f"linha={row_number} {json.dumps(details, ensure_ascii=False)}")
        classification = "CÓPIA_EXATA_NEGÓCIO" if len(signatures) == 1 else "MESMO_ID_DADOS_DIVERGENTES"
        if len(docs) > 1:
            classification += "_DOCS_DIFERENTES"
        print(f"classificacao={classification}")


if __name__ == "__main__":
    main()
