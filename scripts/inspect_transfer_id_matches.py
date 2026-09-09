from __future__ import annotations

import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from domain.models import clean_amount_for_comparison, norm_basic, normalize_date_str
from services.sheets_service import GoogleSheetsService


def _indices(headers):
    return {norm_basic(header): index for index, header in enumerate(headers)}


def _value(row, indices, name):
    index = indices.get(norm_basic(name))
    return row[index].strip() if index is not None and index < len(row) else ""


def _absolute_amount(value):
    normalized = clean_amount_for_comparison(value)
    try:
        return str(abs(Decimal(normalized.replace(",", "."))))
    except Exception:
        return normalized


def main() -> None:
    sheets = GoogleSheetsService(Settings.from_env())
    conta_values = sheets._ws.get_all_values()
    extrato_values = sheets._sh.worksheet("T_EXTRATO").get_all_values()
    conta_indices = _indices(conta_values[0])
    extrato_indices = _indices(extrato_values[0])

    extrato_by_key = defaultdict(list)
    for row_number, row in enumerate(extrato_values[1:], start=2):
        if norm_basic(_value(row, extrato_indices, "TIPO")) != "transferencia":
            continue
        id_interno = _value(row, extrato_indices, "ID_INTERNO")
        if not id_interno:
            continue
        key = (
            normalize_date_str(_value(row, extrato_indices, "DATA MOV.")),
            _absolute_amount(_value(row, extrato_indices, "IMPORTÂNCIA")),
        )
        extrato_by_key[key].append({
            "row": row_number,
            "id_interno": id_interno,
            "descricao": _value(row, extrato_indices, "DESCRIÇÃO"),
            "valor": _value(row, extrato_indices, "IMPORTÂNCIA"),
        })

    results = []
    used_ids = {
        _value(row, conta_indices, "ID_INTERNO")
        for row in conta_values[1:]
        if _value(row, conta_indices, "ID_INTERNO")
    }
    for row_number, row in enumerate(conta_values[1:], start=2):
        if norm_basic(_value(row, conta_indices, "TIPO")) != "transferencia":
            continue
        if _value(row, conta_indices, "ID_INTERNO"):
            continue
        key = (
            normalize_date_str(_value(row, conta_indices, "DATA MOV.")),
            _absolute_amount(_value(row, conta_indices, "IMPORTÂNCIA")),
        )
        candidates = [candidate for candidate in extrato_by_key.get(key, []) if candidate["id_interno"] not in used_ids]
        results.append({
            "contaordem_row": row_number,
            "data": key[0],
            "valor": _value(row, conta_indices, "IMPORTÂNCIA"),
            "descricao": _value(row, conta_indices, "DESCRIÇÃO"),
            "candidates": candidates,
        })

    print(json.dumps({
        "transferencias_sem_id": len(results),
        "matches_unicos": sum(len(item["candidates"]) == 1 for item in results),
        "sem_match": sum(not item["candidates"] for item in results),
        "ambiguos": sum(len(item["candidates"]) > 1 for item in results),
        "items": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
