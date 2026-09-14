"""Corrige divergencias pontuais entre CONTAORDEM e T_EXTRATO.

Por seguranca, a lista de IDs e os campos permitidos sao fechados. Sem --apply,
o script apenas apresenta o plano. DESCRICAO SOMA nunca e alterada.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import gspread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings
from Ronda_Ficheiro import cmp57, mapearColunas


TYPE_IDS = {
    "EXT0000000352",
    "EXT0000000353",
    "EXT0000000588",
    "EXT0000000589",
    "EXT0000000655",
    "EXT0000000720",
    "EXT0000000827",
    "EXT0000000858",
    "EXT0000000958",
    "EXT0000002021",
}

DESCRIPTION_IDS = {
    "EXT0000000570",
    "EXT0000000576",
    "EXT0000000578",
    "EXT0000002158",
}


def required_column(columns: dict[str, int], *names: str) -> int:
    for name in names:
        key = cmp57(name)
        if key in columns:
            return columns[key]
    raise KeyError(f"Coluna obrigatoria nao encontrada: {names}")


def rows_by_id(values: list[list[str]], id_col: int) -> dict[str, tuple[int, list[str]]]:
    result: dict[str, tuple[int, list[str]]] = {}
    duplicates: set[str] = set()
    for row_number, row in enumerate(values[1:], start=2):
        value = row[id_col].strip() if id_col < len(row) else ""
        if not value:
            continue
        if value in result:
            duplicates.add(value)
        result[value] = (row_number, row)
    if duplicates & (TYPE_IDS | DESCRIPTION_IDS):
        raise RuntimeError(f"IDs alvo duplicados: {sorted(duplicates)}")
    return result


def cell(row: list[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    client = gspread.service_account(filename=settings.google_credentials_path)
    spreadsheet = client.open_by_url(settings.spreadsheet_url)
    co_ws = spreadsheet.worksheet(settings.sheet_contaordem)
    origin_ws = spreadsheet.worksheet("T_EXTRATO")

    co_values = co_ws.get_all_values()
    origin_values = origin_ws.get_all_values()
    co_cols = mapearColunas(co_values[0])
    origin_cols = mapearColunas(origin_values[0])

    co_id = required_column(co_cols, "ID_INTERNO", "ID INTERNO")
    co_type = required_column(co_cols, "TIPO")
    co_desc = required_column(co_cols, "DESCRICAO", "DESCRIÇÃO")
    origin_id = required_column(origin_cols, "ID_INTERNO", "ID INTERNO")
    origin_type = required_column(origin_cols, "TIPO")
    origin_desc = required_column(origin_cols, "DESCRICAO", "DESCRIÇÃO")

    co_rows = rows_by_id(co_values, co_id)
    origin_rows = rows_by_id(origin_values, origin_id)
    missing = sorted((TYPE_IDS | DESCRIPTION_IDS) - (co_rows.keys() & origin_rows.keys()))
    if missing:
        raise RuntimeError(f"IDs alvo ausentes numa das folhas: {missing}")

    origin_updates: list[dict] = []
    co_updates: list[dict] = []

    for item_id in sorted(TYPE_IDS):
        co_row_number, co_row = co_rows[item_id]
        origin_row_number, origin_row = origin_rows[item_id]
        new_value = cell(co_row, co_type).strip()
        old_value = cell(origin_row, origin_type).strip()
        print(f"TIPO {item_id}: T_EXTRATO!{origin_row_number} {old_value!r} -> {new_value!r}")
        if old_value != new_value:
            origin_updates.append(
                {"range": f"{gspread.utils.rowcol_to_a1(origin_row_number, origin_type + 1)}", "values": [[new_value]]}
            )

    for item_id in sorted(DESCRIPTION_IDS):
        co_row_number, co_row = co_rows[item_id]
        _, origin_row = origin_rows[item_id]
        new_value = cell(origin_row, origin_desc).strip()
        old_value = cell(co_row, co_desc).strip()
        print(f"DESCRICAO {item_id}: CONTAORDEM!{co_row_number} {old_value!r} -> {new_value!r}")
        if old_value != new_value:
            co_updates.append(
                {"range": f"{gspread.utils.rowcol_to_a1(co_row_number, co_desc + 1)}", "values": [[new_value]]}
            )

    print(f"Alteracoes planeadas: TIPO={len(origin_updates)}, DESCRICAO={len(co_updates)}")
    print("DESCRICAO SOMA: 0 alteracoes")
    if not args.apply:
        print("Simulacao concluida; use --apply para gravar.")
        return

    if origin_updates:
        origin_ws.batch_update(origin_updates, value_input_option="USER_ENTERED")
    if co_updates:
        co_ws.batch_update(co_updates, value_input_option="USER_ENTERED")
    print("Atualizacoes aplicadas com sucesso.")


if __name__ == "__main__":
    main()
