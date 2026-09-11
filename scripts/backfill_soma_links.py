"""Preenche LINK na CONTAORDEM para DOC. SOMA com exatamente sete dígitos."""

from __future__ import annotations

import argparse
import re

import gspread
from gspread.utils import ValueInputOption, ValueRenderOption

from config.settings import Settings
from domain.models import norm_basic


BASE_URL = "https://verbodavida.info/IVV/?mod=ivv&exec=entradas_saidas_dados&ID="


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Grava as fórmulas na planilha")
    args = parser.parse_args()

    settings = Settings.from_env()
    client = gspread.service_account(filename=settings.google_credentials_path)
    worksheet = client.open_by_url(settings.spreadsheet_url).worksheet(settings.sheet_contaordem)
    rows = worksheet.get_all_values(value_render_option=ValueRenderOption.formula)
    if not rows:
        raise RuntimeError("CONTAORDEM está vazia")

    header_map = {norm_basic(value): index for index, value in enumerate(rows[0])}
    doc_col = header_map.get(norm_basic("DOC. SOMA"))
    link_col = header_map.get(norm_basic("LINK"))
    if doc_col is None or link_col is None:
        raise RuntimeError("Colunas DOC. SOMA e LINK são obrigatórias")

    updates = []
    eligible = 0
    already_correct = 0
    for row_number, row in enumerate(rows[1:], start=2):
        doc_id = str(row[doc_col]).strip() if doc_col < len(row) else ""
        if not re.fullmatch(r"\d{7}", doc_id):
            continue
        eligible += 1
        formula = f'=HYPERLINK("{BASE_URL}{doc_id}";"ACESSAR SOMA")'
        current = str(row[link_col]).strip() if link_col < len(row) else ""
        if current == formula:
            already_correct += 1
            continue
        updates.append({
            "range": f"{gspread.utils.rowcol_to_a1(row_number, link_col + 1)}",
            "values": [[formula]],
        })

    print(f"Elegíveis (DOC. SOMA com 7 dígitos): {eligible}")
    print(f"Links já corretos: {already_correct}")
    print(f"Links a atualizar: {len(updates)}")
    if not args.apply:
        print("Prévia concluída; nenhuma célula foi alterada.")
        return 0

    for start in range(0, len(updates), 500):
        worksheet.batch_update(
            updates[start : start + 500],
            value_input_option=ValueInputOption.user_entered,
        )
    print(f"Backfill concluído: {len(updates)} links atualizados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
