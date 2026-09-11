from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import (
    clean_amount_for_comparison,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
    strip_suffix_n,
)
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    values = sheets._ws.get_all_values()
    headers = values[0]
    indices = {norm_basic(header): index for index, header in enumerate(headers)}

    def value(row, field):
        index = indices.get(norm_basic(field))
        return row[index].strip() if index is not None and index < len(row) else ""

    rows_by_doc = defaultdict(list)
    for row_number, row in enumerate(values[1:], start=2):
        doc = normalize_document_value(value(row, "DOC. SOMA"))
        if doc.isdigit():
            rows_by_doc[doc].append((row_number, row))
    target_rows = [item for rows in rows_by_doc.values() if len(rows) > 1 for item in rows]
    target_numbers = {row_number for row_number, _ in target_rows}
    unavailable_docs = {
        normalize_document_value(value(row, "DOC. SOMA"))
        for row_number, row in enumerate(values[1:], start=2)
        if row_number not in target_numbers and normalize_document_value(value(row, "DOC. SOMA")).isdigit()
    }

    planned = []
    allocated_docs = set()
    for row_number, row in target_rows:
        description = value(row, "DESCRIÇÃO SOMA") or value(row, "DESCRIÇÃO")
        data_mov = normalize_date_str(value(row, "DATA MOV."))
        amount = clean_amount_for_comparison(value(row, "IMPORTÂNCIA"))
        movement_type = norm_basic(value(row, "TIPO"))
        candidates = audit.search_by_descricao(description, data_mov=data_mov)
        if not candidates:
            base_description = strip_suffix_n(description)
            if base_description != description:
                candidates = audit.search_by_descricao(base_description, data_mov=data_mov)

        valid = []
        expected_description = norm_basic(strip_suffix_n(description))
        for candidate in candidates:
            candidate_doc = normalize_document_value(candidate.codigo)
            if not candidate_doc.isdigit():
                continue
            if candidate_doc in unavailable_docs or candidate_doc in allocated_docs:
                continue
            if normalize_date_str(candidate.data) != data_mov:
                continue
            if clean_amount_for_comparison(candidate.valor) != amount:
                continue
            if norm_basic(candidate.tipo) != movement_type:
                continue
            if norm_basic(strip_suffix_n(candidate.descricao)) != expected_description:
                continue
            if candidate_doc not in {item.codigo for item in valid}:
                valid.append(candidate)

        if len(valid) == 1:
            new_doc = normalize_document_value(valid[0].codigo)
            allocated_docs.add(new_doc)
            resolution = "ENCONTRADO"
        elif not valid:
            new_doc = "Analisar"
            resolution = "NAO_ENCONTRADO"
        else:
            new_doc = "Analisar"
            resolution = "AMBIGUO"
        planned.append((row_number, new_doc, resolution, len(valid)))
        print(
            f"Linha {row_number}: {resolution} -> {new_doc} "
            f"({data_mov}, {amount}, candidatos={len(valid)})"
        )

    print(
        f"Resumo: alvos={len(planned)} encontrados={sum(item[2] == 'ENCONTRADO' for item in planned)} "
        f"analisar={sum(item[1] == 'Analisar' for item in planned)}"
    )
    if not args.apply:
        print("Simulação: nenhuma atualização aplicada.")
        return

    doc_column = indices[norm_basic("DOC. SOMA")] + 1
    updates = [
        {"range": f"{sheets._col_letter(doc_column)}{row_number}", "values": [[new_doc]]}
        for row_number, new_doc, _, _ in planned
    ]
    if updates:
        sheets._ws.batch_update(updates)
    print(f"Atualizações aplicadas: {len(updates)}")


if __name__ == "__main__":
    main()
