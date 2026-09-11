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
from domain.models import clean_amount_for_comparison, norm_basic
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService
from services.soma_api_service import SomaApiService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected", type=int, required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    rows = sheets.get_all_rows(only_entrada_saida=True)
    dates = sorted({row.data_mov for row in rows if row.auditoria.startswith("Erro SOMA: ausentes=")})
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    audit = AuditService(settings, http, sheets)
    soma = SomaApiService(settings, http)
    missing_rows = []

    for date in dates:
        date_rows = [row for row in rows if row.data_mov == date]
        site_counter = Counter(
            (norm_basic(item.tipo), norm_basic(item.descricao), clean_amount_for_comparison(item.valor))
            for item in audit.search_by_periodo(date)
            if norm_basic(item.tipo) in ("entrada", "saida")
        )
        for row in date_rows:
            key = (
                norm_basic(row.tipo.value),
                norm_basic(row.descricao_soma or row.descricao),
                clean_amount_for_comparison(row.importancia),
            )
            if site_counter[key]:
                site_counter[key] -= 1
            else:
                missing_rows.append(row)

    print(f"Linhas a criar={len(missing_rows)}", flush=True)
    if len(missing_rows) != args.expected:
        raise RuntimeError(f"Criação cancelada: esperadas {args.expected} linhas, encontradas {len(missing_rows)}")
    if not args.apply:
        print("Simulação: nenhuma criação aplicada.")
        return

    updates = []
    for index, row in enumerate(missing_rows, start=1):
        outcome = soma.criar_entrada(row) if norm_basic(row.tipo.value) == "entrada" else soma.criar_saida(row)
        if not outcome.success or not outcome.doc_id.isdigit():
            raise RuntimeError(f"Falha ao criar linha {row.row_number}: {outcome}")
        paid = audit.insert_soma_payment(
            outcome.doc_id,
            row.data_mov,
            row.importancia,
            row.caixa,
            row.forma_pagamento,
        )
        if not paid:
            raise RuntimeError(f"DOC {outcome.doc_id} criado, mas pagamento/baixa não foi confirmado")
        updates.append({
            "row_idx": row.row_number,
            "new_doc": outcome.doc_id,
            "dados_doc": outcome.dados_doc,
            "auditoria": "Criado no SOMA; pendente revalidação do lote",
        })
        sheets.batch_update_audit_records([updates[-1]])
        print(f"Criado {index}/{args.expected} linha={row.row_number} DOC={outcome.doc_id}", flush=True)

    print(f"Criação concluída={args.expected}", flush=True)


if __name__ == "__main__":
    main()
