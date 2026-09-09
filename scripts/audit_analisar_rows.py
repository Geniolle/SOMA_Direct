from __future__ import annotations

import argparse
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from workflows.orchestrator import DirectOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    orchestrator = DirectOrchestrator()
    targets = [
        row
        for row in orchestrator.sheets.get_all_rows(only_entrada_saida=True)
        if row.doc_soma.strip().upper() == "ANALISAR"
    ]
    print(f"Linhas elegíveis: {len(targets)}")
    for row in targets:
        print(
            f"Linha {row.row_number}: {row.data_mov} | {row.tipo.value} | "
            f"{row.importancia} | {row.descricao_soma or row.descricao}"
        )

    if not args.apply:
        print("Listagem somente leitura; audit_row não executado.")
        return

    outcomes = orchestrator.audit_target_rows(
        [row.row_number for row in targets],
        dry_run=False,
    )
    confirmed = sum(outcome.confirmed for outcome in outcomes)
    corrected = sum(outcome.corrected for outcome in outcomes)
    inconsistent = sum(outcome.inconsistent for outcome in outcomes)
    print(
        f"Resultado: confirmados={confirmed} corrigidos={corrected} "
        f"inconsistentes={inconsistent}"
    )


if __name__ == "__main__":
    main()
