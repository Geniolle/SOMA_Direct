from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Planeia ou aplica a reconciliação SOMA Direct."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica as alterações planeadas (sem esta opção, executa em dry-run).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dry_run = not args.apply
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("Modo: DRY-RUN (nenhuma alteração será gravada)" if dry_run else "Modo: APPLY")
    plan = ReconciliationOrchestrator().run(dry_run=dry_run)
    summary = plan.summary()
    print(
        "Plano: "
        f"CONTAORDEM={summary['contaordem']}, "
        f"origem={summary['origem']}, SOMA={summary['soma']}, "
        f"total={summary['total']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
