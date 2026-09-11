from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from domain.models import norm_basic
from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


RECORDS_BY_DATE = {
    "07/01/2024": {
        "EXT0000000010": ("07/01/2024", "TRF.CRED  FELIPE XAVIER", "110,00"),
        "EXT0000000011": ("07/01/2024", "TRF.CRED  PATRICIA R S CUNHA", "10,00"),
        "EXT0000000012": ("07/01/2024", "TRF.CRED WILIAM JOSE S FILHO", "15,00"),
        "EXT0000000013": ("07/01/2024", "TRF.CRED  MAGNUM LUCCAS MACIEL", "6,00"),
    },
    "19/08/2024": {
        "EXT0000000582": ("19/08/2024", "COM. TRANSF. SALDO DO ASS", "-3,00"),
        "EXT0000000583": ("19/08/2024", "PRÉPAGO I.SELO S/COM.", "-0,12"),
        "EXT0000000584": ("19/08/2024", "TRANSF.CART.PREPAGO", "208,76"),
    },
    "10/10/2025": {
        "EXT0000002157": ("10/10/2025", "PAGSERV 21796 415612742", "-69,04"),
        "EXT0000002158": ("10/10/2025", "TRF.IPS P/ WORD OF LIFE INT DI", "-100,00"),
        "EXT0000002159": ("10/10/2025", "EMISSAOEXTR.CONTA-2025-10-09", "-1,00"),
        "EXT0000002160": ("10/10/2025", "I.SELOOP.BANC.-2025-10-09", "-0,04"),
    },
    "11/10/2025": {
    "EXT0000002161": ("11/10/2025", "EMISSAOEXTR.CONTA-2025-10-10", "-1,00"),
    "EXT0000002162": ("11/10/2025", "I.SELOOP.BANC.-2025-10-10", "-0,04"),
    "EXT0000002163": ("12/10/2025", "TRF.CRED RUBENS BARBOSA", "5,00"),
    "EXT0000002164": ("12/10/2025", "TRF.CRED MARIANA CRUZ MAURICIO", "200,00"),
    "EXT0000002165": ("12/10/2025", "TRF.CRED JOAO DUARTE", "2,00"),
    },
}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="11/10/2025")
    args = parser.parse_args()
    records = RECORDS_BY_DATE[args.date]
    orchestrator = ReconciliationOrchestrator()
    conta_values = orchestrator.sheets._ws.get_all_values()
    conta_indices = {norm_basic(header): index for index, header in enumerate(conta_values[0])}

    def conta_value(row, field):
        index = conta_indices[norm_basic(field)]
        return row[index].strip() if index < len(row) else ""

    conta_by_id = {
        conta_value(row, "ID_INTERNO"): row
        for row in conta_values[1:]
        if conta_value(row, "ID_INTERNO") in records
    }
    if set(conta_by_id) != set(records):
        raise RuntimeError(f"IDs ausentes na CONTAORDEM: {sorted(set(records) - set(conta_by_id))}")

    worksheet = orchestrator._get_source_worksheet("T_EXTRATO")
    source_values = worksheet.get_all_values()
    headers = source_values[0]
    source_indices = {norm_basic(header): index for index, header in enumerate(headers)}
    id_index = source_indices[norm_basic("ID_INTERNO")]
    existing_ids = {row[id_index].strip() for row in source_values[1:] if id_index < len(row)}
    duplicates = set(records) & existing_ids
    if duplicates:
        raise RuntimeError(f"Criação cancelada; IDs já existentes no T_EXTRATO: {sorted(duplicates)}")

    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    rows = []
    for internal_id, (value_date, description, amount) in records.items():
        conta_row = conta_by_id[internal_id]
        row = [""] * len(headers)

        def set_value(field, value, occurrence=0):
            matches = [index for index, header in enumerate(headers) if norm_basic(header) == norm_basic(field)]
            if len(matches) > occurrence:
                row[matches[occurrence]] = value

        set_value("DOC. SOMA", conta_value(conta_row, "DOC. SOMA"))
        set_value("DATA MOV.", args.date)
        set_value("DATA VALOR", value_date)
        set_value("DESCRIÇÃO", description)
        set_value("IMPORTÂNCIA", amount)
        set_value("TIPO", conta_value(conta_row, "TIPO"))
        set_value("ID_INTERNO", internal_id)
        set_value("TIMESTAMP", timestamp)
        set_value("DESCRIÇÃO SOMA", conta_value(conta_row, "DESCRIÇÃO SOMA"))
        rows.append(row)

    worksheet.append_rows(rows, value_input_option="USER_ENTERED")
    print(f"Registos criados no T_EXTRATO: {len(rows)}")
    print("IDs=" + ",".join(records))


if __name__ == "__main__":
    main()
