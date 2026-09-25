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

from workflows.repasse_orchestrator import RepasseAuditOrchestrator, print_repasse_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audita repasses SOMA contra o Balancete.")
    parser.add_argument("--ano", type=int, required=True, help="Ano do relatório de repasses")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Consulta e reconcilia sem escrever na T_REPASSE (padrão)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Grava/atualiza a T_REPASSE. Sem esta opção, executa em dry-run.",
    )
    parser.add_argument(
        "--instituicao",
        default=None,
        help="ID da instituição SOMA. Se omitido, usa INSTITUTION_ID/configuração.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dry_run = args.dry_run or not args.apply
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("Modo: DRY-RUN (nenhuma alteração será gravada)" if dry_run else "Modo: GRAVAÇÃO T_REPASSE")
    records = RepasseAuditOrchestrator().run(
        args.ano,
        dry_run=dry_run,
        instituicao_id=args.instituicao,
    )
    print_repasse_summary(records, args.ano)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
