from __future__ import annotations

import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


ROWS = {2981: "5004129", 1106: "5005907", 1929: "4614499", 4318: "4623459"}


def response_status(response) -> int:
    try:
        return int(response.json().get("status", 0))
    except Exception:
        return 0


def main() -> None:
    settings = Settings.from_env()
    base_url = settings.site_base_url.rstrip("/")
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    rows = {row.row_number: row for row in sheets.get_all_rows(only_entrada_saida=True)}

    for row_number, document_code in ROWS.items():
        row = rows[row_number]
        page = http.get(f"{base_url}/?mod=ivv&exec=entradas_saidas_dados&ID={document_code}")
        payment_ids = re.findall(
            r'<input\s+id="(\d+)"[^>]*class="[^"]*pagamentos_check',
            page.text,
            re.IGNORECASE,
        )
        if len(payment_ids) != 1:
            raise RuntimeError(f"DOC {document_code}: esperado 1 pagamento, encontrados {payment_ids}")
        payment_id = payment_ids[0]

        baixa_response = http.post(f"{base_url}/sys/app/baixas.php", data={"id": payment_id, "excluir": "1"})
        baixa_status = response_status(baixa_response)
        if baixa_status not in (1, 4):
            raise RuntimeError(f"DOC {document_code}: falha ao excluir baixa, status={baixa_status}")

        payment_response = http.post(
            f"{base_url}/sys/app/pagamentos.php",
            data={"id": payment_id, "excluir": "1", "id_f": f"{int(document_code):010d}"},
        )
        payment_status = response_status(payment_response)
        if payment_status not in (1, 4):
            raise RuntimeError(f"DOC {document_code}: falha ao excluir pagamento, status={payment_status}")

        if not audit.insert_soma_payment(
            document_code,
            row.data_mov,
            row.importancia,
            row.caixa or row.caixa_saida,
            row.forma_pagamento,
        ):
            raise RuntimeError(f"DOC {document_code}: novo pagamento/baixa não confirmado")
        print(f"Pagamento corrigido linha={row_number} DOC={document_code}", flush=True)


if __name__ == "__main__":
    main()
