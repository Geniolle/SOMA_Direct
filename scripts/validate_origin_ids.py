from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


def main() -> None:
    report = ReconciliationOrchestrator().validate_origin_ids()
    print(json.dumps(report.summary(), ensure_ascii=False, indent=2))

    for source in report.sources:
        print(f"\n[{source.source_sheet}]")
        if source.error:
            print(f"ERRO: {source.error}")
            continue
        print(
            f"origem_lidas={source.rows_checked} "
            f"encontradas_na_contaordem={source.found} "
            f"ausentes_na_contaordem={source.missing} "
            f"contaordem_lidas={source.contaordem_rows_checked} "
            f"ausentes_na_origem={source.missing_in_source}"
        )
        duplicates = [item for item in source.items if "DUPLICADO" in item.status]
        if not duplicates:
            print("Sem duplicados.")
            continue
        for item in duplicates:
            print(
                f"{item.status}: ID={item.id_interno} "
                f"origem_linha={item.source_row} "
                f"contaordem_linhas={item.contaordem_rows}"
            )

    if any(source.error for source in report.sources):
        titles = [worksheet.title for worksheet in ReconciliationOrchestrator().sheets._sh.worksheets()]
        print("\nFolhas disponíveis:")
        print(" | ".join(titles))


if __name__ == "__main__":
    main()
