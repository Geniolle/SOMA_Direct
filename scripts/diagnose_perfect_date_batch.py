from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import norm_basic, normalize_date_str
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService
from workflows.reconciliation_orchestrator import ReconciliationOrchestrator, SOURCE_SHEETS


def main() -> None:
    target_date = sys.argv[1]
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    SomaAuthenticator(settings, http).login()
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    values = sheets._ws.get_all_values()
    indices = {norm_basic(header): index for index, header in enumerate(values[0])}

    def value(row, field):
        index = indices.get(norm_basic(field))
        return row[index].strip() if index is not None and index < len(row) else ""

    print("CONTAORDEM")
    for row_number, row in enumerate(values[1:], start=2):
        if normalize_date_str(value(row, "DATA MOV.")) == target_date and norm_basic(value(row, "TIPO")) in ("entrada", "saida"):
            print(row_number, value(row, "TIPO"), value(row, "DESCRIÇÃO SOMA"), value(row, "IMPORTÂNCIA"), value(row, "DOC. SOMA"), value(row, "ID_INTERNO"))

    print("SOMA")
    for item in audit.search_by_periodo(target_date):
        if norm_basic(item.tipo) in ("entrada", "saida"):
            print(item.codigo, item.tipo, item.descricao, item.valor, item.data, item.status, item.baixa)

    print("ORIGENS")
    orchestrator = ReconciliationOrchestrator(sheets=sheets)
    for source_name in SOURCE_SHEETS:
        worksheet = orchestrator._get_source_worksheet(source_name)
        source_values = worksheet.get_all_values()
        source_indices = {norm_basic(header): index for index, header in enumerate(source_values[0])}
        date_field = "DATA MOV." if source_name == "T_EXTRATO" else "DATA"
        for row_number, row in enumerate(source_values[1:], start=2):
            def source_value(field):
                index = source_indices.get(norm_basic(field))
                return row[index].strip() if index is not None and index < len(row) else ""
            if normalize_date_str(source_value(date_field)) == target_date:
                print(source_name, row_number, source_value("ID_INTERNO"), source_value("TIPO"), source_value("IMPORTÂNCIA") or source_value("VALOR") or source_value("VALOR DA COMPRA") or source_value("MONTANTE") or source_value("VALOR A PAGAR"), source_value("DESCRIÇÃO") or source_value("DESCRIÇÃO SOMA"), source_value("FORMA DE PAGAMENTO"))


if __name__ == "__main__":
    main()
