from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gspread.utils import rowcol_to_a1

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from domain.models import norm_basic, normalize_date_str
from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    orchestrator = ReconciliationOrchestrator()
    conta_values = orchestrator.sheets._ws.get_all_values()
    conta_indices = {norm_basic(header): index for index, header in enumerate(conta_values[0])}

    def conta_value(row, field):
        index = conta_indices[norm_basic(field)]
        return row[index].strip() if index < len(row) else ""

    error_dates = {
        normalize_date_str(conta_value(row, "DATA MOV."))
        for row in conta_values[1:]
        if conta_value(row, "AUDITORIA").startswith("Erro: Quantidade T_EXTRATO")
    }

    source_values = orchestrator._get_source_worksheet("T_EXTRATO").get_all_values()
    source_indices = {norm_basic(header): index for index, header in enumerate(source_values[0])}

    def source_value(row, field):
        index = source_indices[norm_basic(field)]
        return row[index].strip() if index < len(row) else ""

    source_dates = {
        source_value(row, "ID_INTERNO"): normalize_date_str(source_value(row, "DATA MOV."))
        for row in source_values[1:]
        if source_value(row, "ID_INTERNO")
    }

    corrections = []
    affected_dates = set()
    for row_number, row in enumerate(conta_values[1:], start=2):
        if norm_basic(conta_value(row, "PROCESSO")) != "t_extrato":
            continue
        internal_id = conta_value(row, "ID_INTERNO")
        current_date = normalize_date_str(conta_value(row, "DATA MOV."))
        source_date = source_dates.get(internal_id, "")
        if not source_date or current_date == source_date:
            continue
        if current_date not in error_dates and source_date not in error_dates:
            continue
        corrections.append((row_number, internal_id, current_date, source_date))
        affected_dates.update((current_date, source_date))

    print(f"Datas com erro={len(error_dates)} correções={len(corrections)}")
    for row_number, internal_id, current_date, source_date in corrections:
        print(f"Linha={row_number} ID={internal_id} {current_date} -> {source_date}")

    if args.apply and corrections:
        date_column = conta_indices[norm_basic("DATA MOV.")] + 1
        orchestrator.sheets._ws.batch_update([
            {"range": rowcol_to_a1(row_number, date_column), "values": [[source_date]]}
            for row_number, _, _, source_date in corrections
        ])
        print("Correções aplicadas.")
    else:
        print("Simulação: nenhuma alteração aplicada.")
    print("Datas afetadas=" + ",".join(sorted(affected_dates)))


if __name__ == "__main__":
    main()
