from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import clean_amount_for_comparison, norm_basic, normalize_date_str
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


CORRECTIONS = {
    1466: "5496662",
    2497: "4581530",
    2579: "5496669",
    3242: "4617359",
    3243: "4593223",
    3245: "5486941",
    3246: "5486942",
    3247: "5486943",
}


def main() -> None:
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    rows_by_number = {row.row_number: row for row in sheets.get_all_rows(only_entrada_saida=True)}
    updates = []

    for row_number, document_code in CORRECTIONS.items():
        row = rows_by_number[row_number]
        document = audit.search_by_codigo(document_code)
        if document is None:
            raise RuntimeError(f"DOC {document_code} não encontrado para a linha {row_number}")
        differences = []
        if normalize_date_str(document.data) != normalize_date_str(row.data_mov):
            differences.append(f"data {document.data} != {row.data_mov}")
        if norm_basic(document.descricao) != norm_basic(row.descricao_soma or row.descricao):
            differences.append(f"descrição {document.descricao} != {row.descricao_soma or row.descricao}")
        if clean_amount_for_comparison(document.valor) != clean_amount_for_comparison(row.importancia):
            differences.append(f"valor {document.valor} != {row.importancia}")
        if differences:
            raise RuntimeError(f"DOC {document_code} inválido para linha {row_number}: {'; '.join(differences)}")
        updates.append({"row_idx": row_number, "new_doc": document_code, "auditoria": "Concluido"})
        print(f"Verificado linha={row_number} DOC={document_code}")

    updates.append({
        "row_idx": 3289,
        "new_doc": "",
        "auditoria": "Erro final: sem correspondência no SOMA para data + descrição; DOC 4617359 pertence a 30/11/2024",
    })
    sheets.batch_update_audit_records(updates)
    print(f"DOCs corrigidos={len(CORRECTIONS)}; DOC incorreto removido da linha 3289")


if __name__ == "__main__":
    main()
