from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import clean_amount_for_comparison, norm_basic, normalize_date_str, normalize_document_value
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


def main() -> None:
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    rows = [
        row for row in sheets.get_all_rows(only_entrada_saida=True)
        if row.auditoria.startswith(("Erro final:", "Erro DOC:"))
    ]

    soma_by_date = {}
    for date in sorted({normalize_date_str(row.data_mov) for row in rows}):
        soma_by_date[date] = audit.search_by_periodo(date)

    grouped = defaultdict(list)
    for row in rows:
        date = normalize_date_str(row.data_mov)
        description = norm_basic(row.descricao_soma or row.descricao)
        expected_doc = normalize_document_value(row.doc_soma)
        candidates = [item for item in soma_by_date[date] if norm_basic(item.descricao) == description]
        document = audit.search_by_codigo(expected_doc) if expected_doc else None
        grouped[date].append((row, candidates, document))

    for date in sorted(grouped, reverse=True):
        print(f"\nDATA {date}")
        for row, candidates, document in grouped[date]:
            candidate_text = "; ".join(
                f"DOC={item.codigo} valor={item.valor} data={item.data} desc={item.descricao}"
                for item in candidates
            ) or "nenhum"
            document_text = (
                f"DOC={document.codigo} valor={document.valor} data={document.data} desc={document.descricao} status={document.status} baixa={document.baixa}"
                if document
                else "não encontrado"
            )
            print(
                f"Linha={row.row_number} ID={row.id_interno} tipo={row.tipo.value} "
                f"valor={row.importancia} DOC={row.doc_soma} caixa={row.caixa or row.caixa_saida} "
                f"forma={row.forma_pagamento} desc={row.descricao_soma or row.descricao}"
            )
            print(f"  Erro={row.auditoria}")
            print(f"  Candidatos data+descrição={candidate_text}")
            print(f"  Documento informado={document_text}")

    print(f"\nTotal analisado={len(rows)}")


if __name__ == "__main__":
    main()
