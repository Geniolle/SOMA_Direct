from __future__ import annotations

import argparse
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    orchestrator = ReconciliationOrchestrator()
    plan = orchestrator.build_transfer_id_plan()
    print(f"Correspondências únicas: {len(plan.matches)}")
    print(f"Sem correspondência: {len(plan.unmatched_rows)} {plan.unmatched_rows}")
    print(f"Ambíguas: {len(plan.ambiguous_rows)} {plan.ambiguous_rows}")
    for match in plan.matches:
        print(
            f"CONTAORDEM {match.contaordem_row} <- T_EXTRATO {match.extrato_row}: "
            f"{match.id_interno} ({match.data_mov}, {match.valor})"
        )
    if args.apply:
        print(f"Atualizações aplicadas: {orchestrator.apply_transfer_id_plan(plan)}")
    else:
        print("Simulação: nenhuma atualização aplicada.")


if __name__ == "__main__":
    main()
