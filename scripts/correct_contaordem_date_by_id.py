from __future__ import annotations

import argparse
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from domain.models import norm_basic, normalize_date_str
from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    target_date = normalize_date_str(args.date)
    orchestrator = ReconciliationOrchestrator()
    values = orchestrator.sheets._ws.get_all_values()
    indices = {norm_basic(header): index for index, header in enumerate(values[0])}
    id_index = indices[norm_basic("ID_INTERNO")]
    date_index = indices[norm_basic("DATA MOV.")]
    matches = [
        (row_number, row)
        for row_number, row in enumerate(values[1:], start=2)
        if id_index < len(row) and row[id_index].strip() == args.id
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Esperado 1 registo para {args.id}; encontrados {len(matches)}")

    row_number, row = matches[0]
    current_date = row[date_index].strip() if date_index < len(row) else ""
    print(f"Linha={row_number} ID_INTERNO={args.id} data_atual={current_date} nova_data={target_date}")
    if not args.apply:
        print("Simulação: nenhuma alteração aplicada.")
        return
    orchestrator.sheets._ws.update_cell(row_number, date_index + 1, target_date)
    print("Data atualizada.")


if __name__ == "__main__":
    main()
