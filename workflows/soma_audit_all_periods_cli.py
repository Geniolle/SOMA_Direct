from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
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


def build_period_command(python: str, batch_cli: Path, period: str) -> list[str]:
    return [
        python,
        str(batch_cli),
        "--periodo",
        period,
        "--apply",
        "--confirm",
        "APPLY-SOMA-BATCH",
    ]


def run_periods(
    periods: Sequence[str],
    runner: Callable[[str], int],
    on_complete: Callable[[list[str], str | None], None] | None = None,
) -> tuple[list[str], str | None]:
    completed: list[str] = []
    for period in periods:
        if runner(period) != 0:
            if on_complete:
                on_complete(completed, period)
            return completed, period
        completed.append(period)
        if on_complete:
            on_complete(completed, None)
    return completed, None


def _write_progress(path: Path, periods: list[str], completed: list[str], failed: str | None) -> None:
    payload = {
        "started_periods": periods,
        "completed_periods": completed,
        "failed_period": failed,
        "state": "FAILED" if failed else ("COMPLETED" if len(completed) == len(periods) else "RUNNING"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    settings = Settings.from_env()
    http = ResilientSession(verify_tls=settings.verify_tls)
    authenticator = SomaAuthenticator(settings, http)
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    discovery = SomaAuditBatchOrchestrator(sheets, audit, authenticator.login)
    periods = list(discovery.available_periods())
    progress_path = PROJECT_ROOT / "logs" / "soma_audit_batch" / "all_periods_progress.json"
    lock_path = PROJECT_ROOT / ".soma_audit_all_periods.lock"
    batch_cli = PROJECT_ROOT / "workflows" / "soma_audit_batch_cli.py"

    with orchestrator_session(lock_path) as acquired:
        if not acquired:
            raise SystemExit("Outro supervisor SOMA por períodos já está ativo")
        _write_progress(progress_path, periods, [], None)

        def runner(period: str) -> int:
            command = build_period_command(sys.executable, batch_cli, period)
            return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode

        completed, failed = run_periods(
            periods,
            runner,
            lambda done, error: _write_progress(progress_path, periods, done, error),
        )
    print(json.dumps({"periods": periods, "completed": completed, "failed": failed}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
