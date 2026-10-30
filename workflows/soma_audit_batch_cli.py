from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from config.paths import PROJECT_ROOT
from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from core.run_lock import orchestrator_session
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService
from workflows.soma_audit_batch import SomaAuditBatchOrchestrator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auditoria geral SOMA da CONTAORDEM.")
    parser.add_argument("--apply", action="store_true", help="Executa operações financeiras e escreve apenas CONTAORDEM.SOMA.")
    parser.add_argument(
        "--no-financial-mutations",
        action="store_true",
        help="Permite escritas finais, mas bloqueia pagamentos e baixas.",
    )
    parser.add_argument("--confirm", default="", help="Confirmação exigida no modo apply.")
    parser.add_argument("--periodo", help="Período obrigatório no formato MM/AAAA.")
    parser.add_argument("--report", type=Path, default=None, help="Caminho do relatório JSON.")
    parser.add_argument("--checkpoint", type=Path, default=None)
    return parser


def validate_apply_confirmation(args: argparse.Namespace) -> None:
    if args.apply and args.confirm != "APPLY-SOMA-BATCH":
        raise SystemExit("Modo apply exige --confirm APPLY-SOMA-BATCH")


def period_directory(period: str) -> Path:
    month, year = period.split("/")
    return PROJECT_ROOT / "logs" / "soma_audit_batch" / f"{year}-{month}"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_apply_confirmation(args)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    settings = Settings.from_env()
    http = ResilientSession(verify_tls=settings.verify_tls)
    authenticator = SomaAuthenticator(settings, http)
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    discovery = SomaAuditBatchOrchestrator(sheets, audit, authenticator.login)
    if not args.periodo:
        periods = discovery.available_periods()
        print("Períodos disponíveis (registos elegíveis):")
        for period, count in periods.items():
            print(f"{period}: {count}")
        return 0
    try:
        period = discovery._normalize_period(args.periodo)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    period_dir = period_directory(period)
    checkpoint_path = args.checkpoint or period_dir / "checkpoint.json"
    runner = SomaAuditBatchOrchestrator(sheets, audit, authenticator.login, checkpoint_path)
    report_path = args.report or period_dir / f"report_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
    lock_path = PROJECT_ROOT / ".soma_audit_batch.lock"
    with orchestrator_session(lock_path) as acquired:
        if not acquired:
            raise SystemExit("Outra auditoria SOMA em lote já está ativa")
        report = runner.run(
            apply=args.apply,
            periodo=period,
            allow_financial_mutations=not args.no_financial_mutations,
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    print(f"Relatório: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
