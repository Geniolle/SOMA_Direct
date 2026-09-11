from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from workflows.reconciliation_orchestrator import ReconciliationOrchestrator, SOURCE_SHEETS


def main() -> None:
    orchestrator = ReconciliationOrchestrator()
    for source in SOURCE_SHEETS:
        worksheet = orchestrator._get_source_worksheet(source)
        print(f"{source}: {worksheet.row_values(1)}")


if __name__ == "__main__":
    main()
