from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import norm_basic, normalize_document_value, validate_dados_doc
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--start-row", type=int, default=2)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--only-not-confirmed", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    rows = [
        row for row in sheets.get_all_rows(only_entrada_saida=True)
        if row.row_number >= args.start_row and normalize_document_value(row.doc_soma).isdigit()
        and (not args.only_not_confirmed or norm_basic(row.auditoria) != "confirmado")
    ]
    if args.limit is not None:
        rows = rows[:args.limit]

    cache = {}
    updates = []
    counts = Counter()
    for index, row in enumerate(rows, start=1):
        document_code = normalize_document_value(row.doc_soma)
        try:
            if document_code not in cache:
                result = audit.search_by_codigo(document_code)
                cache[document_code] = result
            result = cache[document_code]

            _, inconsistencies = audit.validate_soma_record(row, result)
            if result is not None:
                expected_caixa = row.caixa or row.caixa_saida
                details_valid, details_error = validate_dados_doc(
                    row.dados_doc,
                    expected_caixa,
                    row.forma_pagamento,
                )
                if not details_valid:
                    details = audit.fetch_dados_doc(document_code)
                    details_valid, details_error = validate_dados_doc(
                        details,
                        expected_caixa,
                        row.forma_pagamento,
                    )
                if not details_valid:
                    inconsistencies.append(details_error or "CAIXA/DADOS DOC inválido")

            if inconsistencies:
                status = "Erro DOC: " + " | ".join(inconsistencies)
                counts["Erro"] += 1
            else:
                status = "Confirmado"
                counts["Confirmado"] += 1
        except Exception as exc:
            status = f"Erro técnico DOC {document_code}: {exc}"
            counts["Erro técnico"] += 1

        updates.append({"row_idx": row.row_number, "auditoria": status})
        if args.apply and len(updates) >= args.batch_size:
            sheets.batch_update_audit_records(updates)
            updates.clear()
        if index % 25 == 0 or index == len(rows):
            print(
                f"Progresso={index}/{len(rows)} Confirmado={counts['Confirmado']} "
                f"Erro={counts['Erro']} Erro_técnico={counts['Erro técnico']}",
                flush=True,
            )

    if args.apply and updates:
        sheets.batch_update_audit_records(updates)
    print(
        f"Total={len(rows)} Confirmado={counts['Confirmado']} "
        f"Erro={counts['Erro']} Erro_técnico={counts['Erro técnico']} "
        f"Aplicado={'SIM' if args.apply else 'NÃO'}"
    )


if __name__ == "__main__":
    main()
