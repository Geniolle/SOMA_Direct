from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


def main() -> None:
    report = ReconciliationOrchestrator().validate_origin_ids(("T_EXTRATO",))
    source = report.sources[0]
    for item in source.items:
        if item.status == "NAO_ENCONTRADO":
            print(f"ORIGEM->CONTAORDEM ID={item.id_interno} T_EXTRATO_linha={item.source_row}")
        elif item.status == "NAO_ENCONTRADO_NA_ORIGEM":
            print(
                f"CONTAORDEM->ORIGEM ID={item.id_interno} "
                f"CONTAORDEM_linhas={item.contaordem_rows}"
            )


if __name__ == "__main__":
    main()
