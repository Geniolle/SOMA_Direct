from __future__ import annotations

import argparse
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import (
    clean_amount_for_comparison,
    extract_suffix_n,
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

    used_docs = {
        normalize_document_value(value(row, "DOC. SOMA"))
        for row in values[1:]
        if normalize_document_value(value(row, "DOC. SOMA")).isdigit()
    }
    date_cache = {}
    allocated_docs = set()
    planned = []

    for row_number, row in enumerate(values[1:], start=2):
        if value(row, "DOC. SOMA").strip().upper() != "ANALISAR":
            continue
        data_mov = normalize_date_str(value(row, "DATA MOV."))
        amount = clean_amount_for_comparison(value(row, "IMPORTÂNCIA"))
        movement_type = norm_basic(value(row, "TIPO"))
        description = value(row, "DESCRIÇÃO SOMA") or value(row, "DESCRIÇÃO")
        if data_mov not in date_cache:
            date_cache[data_mov] = audit.search_by_periodo(data_mov)

        candidates = []
        for candidate in date_cache[data_mov]:
            candidate_doc = normalize_document_value(candidate.codigo)
            if not candidate_doc.isdigit() or candidate_doc in used_docs or candidate_doc in allocated_docs:
                continue
            if clean_amount_for_comparison(candidate.valor) != amount:
                continue
            if norm_basic(candidate.tipo) != movement_type:
                continue
            candidates.append(candidate)

        selected = None
        if len(candidates) == 1:
            selected = candidates[0]
        elif candidates:
            exact = [candidate for candidate in candidates if norm_basic(candidate.descricao) == norm_basic(description)]
            if len(exact) == 1:
                selected = exact[0]
            else:
                base = norm_basic(strip_suffix_n(description))
                semantic = [candidate for candidate in candidates if norm_basic(strip_suffix_n(candidate.descricao)) == base]
                row_sequence = extract_suffix_n(description)
                same_sequence = [candidate for candidate in semantic if extract_suffix_n(candidate.descricao) == row_sequence]
                if len(same_sequence) == 1:
                    selected = same_sequence[0]
                elif len(semantic) == 1:
                    selected = semantic[0]

        if selected is not None:
            new_doc = normalize_document_value(selected.codigo)
            allocated_docs.add(new_doc)
            resolution = "ENCONTRADO"
        elif candidates:
            new_doc = "Analisar"
            resolution = "AMBIGUO"
        else:
            new_doc = "Analisar"
            resolution = "NAO_ENCONTRADO"
        planned.append((row_number, new_doc, resolution, len(candidates)))
        print(f"Linha {row_number}: {resolution} -> {new_doc} candidatos_financeiros={len(candidates)}")

    print(
        f"Resumo: alvos={len(planned)} encontrados={sum(item[2] == 'ENCONTRADO' for item in planned)} "
        f"ambiguos={sum(item[2] == 'AMBIGUO' for item in planned)} "
        f"sem_match={sum(item[2] == 'NAO_ENCONTRADO' for item in planned)}"
    )
    if not args.apply:
        print("Simulação: nenhuma atualização aplicada.")
        return

    doc_column = indices[norm_basic("DOC. SOMA")] + 1
    updates = [
        {"range": f"{sheets._col_letter(doc_column)}{row_number}", "values": [[new_doc]]}
        for row_number, new_doc, resolution, _ in planned
        if resolution == "ENCONTRADO"
    ]
    if updates:
        sheets._ws.batch_update(updates)
    print(f"Atualizações aplicadas: {len(updates)}")


if __name__ == "__main__":
    main()
