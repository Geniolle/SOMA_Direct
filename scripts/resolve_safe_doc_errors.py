from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import norm_basic
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


CASH_ROWS = {2154, 2258, 2406, 2412, 2445, 2506, 2633, 2734, 2843, 3012}
OPEN_DOC_ROWS = {2: "5499162", 3: "5499163"}


def main() -> None:
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    rows = {row.row_number: row for row in sheets.get_all_rows(only_entrada_saida=True)}
    updates = []

    for row_number in sorted(CASH_ROWS):
        row = rows[row_number]
        if not row.id_interno.startswith("ENT"):
            raise RuntimeError(f"Linha {row_number} não é DÍZIMOS/OFERTAS: {row.id_interno}")
        updates.append({
            "row_idx": row_number,
            "new_caixa": "CAIXA DIÁRIO",
            "new_forma_pagamento": "DINHEIRO",
            "auditoria": "Pendente revalidação por DOC",
        })

    row = rows[4286]
    target = audit.search_by_codigo("4586712")
    if target is None or norm_basic(target.descricao) != norm_basic(row.descricao_soma or row.descricao):
        raise RuntimeError("DOC 4586712 não foi confirmado para a linha 4286")
    updates.append({"row_idx": 4286, "new_doc": "4586712", "auditoria": "Pendente revalidação por DOC"})
    sheets.batch_update_audit_records(updates)
    print(f"Informações corrigidas={len(CASH_ROWS)}; DOC corrigido linha 4286=4586712")

    for row_number, document_code in OPEN_DOC_ROWS.items():
        row = rows[row_number]
        if not audit.insert_soma_payment(
            document_code,
            row.data_mov,
            row.importancia,
            row.caixa or row.caixa_saida,
            row.forma_pagamento,
        ):
            raise RuntimeError(f"Pagamento/baixa não confirmado para DOC {document_code}")
        print(f"Pagamento e baixa confirmados linha={row_number} DOC={document_code}")


if __name__ == "__main__":
    main()
