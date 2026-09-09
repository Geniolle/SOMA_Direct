from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import datetime
from decimal import Decimal
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from domain.models import clean_amount_for_comparison, norm_basic
from services.sheets_service import GoogleSheetsService


TARGET_IDS = {
    "EXT0000000016",
    "EXT0000000018",
    "EXT0000000019",
    "EXT0000000572",
    "EXT0000000581",
    "EXT0000001727",
}
MONTHS = (
    "JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
)


def compact_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", str(value or "").upper().replace(" ", ""))
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def absolute_amount(value: str) -> str:
    normalized = clean_amount_for_comparison(value)
    amount = abs(Decimal(normalized.replace(",", ".")))
    return f"{amount:.2f}".replace(".", ",")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    sheets = GoogleSheetsService(Settings.from_env())
    source_values = sheets._sh.worksheet("T_EXTRATO").get_all_values()
    target_values = sheets._ws.get_all_values()
    source_indices = {norm_basic(header): index for index, header in enumerate(source_values[0])}
    target_indices = {norm_basic(header): index for index, header in enumerate(target_values[0])}

    def value(row, indices, field):
        index = indices[norm_basic(field)]
        return row[index].strip() if index < len(row) else ""

    existing_ids = {
        value(row, target_indices, "ID_INTERNO")
        for row in target_values[1:]
        if value(row, target_indices, "ID_INTERNO")
    }
    rows_to_append = []
    selected_ids = set()
    for source_row in source_values[1:]:
        id_interno = value(source_row, source_indices, "ID_INTERNO")
        if id_interno not in TARGET_IDS or id_interno in existing_ids:
            continue
        if id_interno in selected_ids:
            raise RuntimeError(f"ID duplicado na origem: {id_interno}")
        selected_ids.add(id_interno)
        data_mov = value(source_row, source_indices, "DATA MOV.")
        target_row = [""] * len(target_values[0])
        mapped = {
            "DATA MOV.": data_mov,
            "DESCRIÇÃO": compact_text(value(source_row, source_indices, "DESCRIÇÃO")),
            "IMPORTÂNCIA": absolute_amount(value(source_row, source_indices, "IMPORTÂNCIA")),
            "TIPO": value(source_row, source_indices, "TIPO"),
            "PERÍODO": MONTHS[datetime.strptime(data_mov, "%d/%m/%Y").month - 1],
            "PROCESSO": "T_EXTRATO",
            "ID_INTERNO": id_interno,
            "DOC. SOMA": "Analisar",
        }
        for field, field_value in mapped.items():
            target_row[target_indices[norm_basic(field)]] = field_value
        rows_to_append.append(target_row)
        print(f"Preparado: {id_interno} | {data_mov} | {mapped['TIPO']} | {mapped['IMPORTÂNCIA']}")

    unresolved = TARGET_IDS - existing_ids - selected_ids
    if unresolved:
        raise RuntimeError(f"IDs não encontrados na T_EXTRATO: {sorted(unresolved)}")
    print(f"Total preparado: {len(rows_to_append)}")
    if args.apply and rows_to_append:
        sheets._ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
        print(f"Total criado: {len(rows_to_append)}")
    else:
        print("Simulação: nenhuma linha criada.")


if __name__ == "__main__":
    main()
