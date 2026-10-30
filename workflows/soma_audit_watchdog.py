from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from config.paths import PROJECT_ROOT
from config.settings import Settings
from core.run_lock import orchestrator_session
from services.sheets_service import GoogleSheetsService
from workflows.soma_audit_batch import SomaAuditBatchOrchestrator

PROGRESS_PATH = PROJECT_ROOT / "logs" / "soma_audit_batch" / "all_periods_progress.json"
GLOBAL_LOG_PATH = PROJECT_ROOT / "logs" / "soma_audit_batch" / "all_periods.log"
WATCHDOG_LOG_PATH = PROJECT_ROOT / "logs" / "soma_audit_batch" / "watchdog.log"


def should_restart(process_running: bool, state: str, uncertain_results: int, log_tail: str) -> bool:
    return (
        not process_running
        and state == "FAILED"
        and uncertain_results == 0
        and "[429]" in log_tail
        and "Quota exceeded for quota metric 'Read requests'" in log_tail
    )


def should_start(
    process_running: bool,
    state: str,
    uncertain_results: int,
    log_tail: str,
    pending_periods: int,
) -> bool:
    if process_running or uncertain_results or pending_periods <= 0:
        return False
    if state in ("COMPLETED", "MISSING", "RUNNING"):
        return True
    return should_restart(process_running, state, uncertain_results, log_tail)


def build_tmux_args(session: str, command: str) -> list[str]:
    return ["tmux", "new-session", "-d", "-s", session, "/bin/bash", "-lc", command]


def _process_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "python.*soma_audit_(all_periods|batch)_cli.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _discover_pending_periods() -> dict[str, int]:
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    discovery = SomaAuditBatchOrchestrator(sheets, audit=None, authenticate=lambda: True)
    return discovery.available_periods()


def _latest_uncertain_results() -> int:
    checkpoints = list((PROJECT_ROOT / "logs" / "soma_audit_batch").glob("*/checkpoint.json"))
    if not checkpoints:
        return 0
    latest = max(checkpoints, key=lambda path: path.stat().st_mtime)
    return int(json.loads(latest.read_text(encoding="utf-8")).get("uncertain_results", 0))


def _log(message: str) -> None:
    WATCHDOG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with WATCHDOG_LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(f"{timestamp} {message}\n")


def main() -> int:
    with orchestrator_session(PROJECT_ROOT / ".soma_audit_watchdog.lock") as acquired:
        if not acquired:
            return 0
        running = _process_running()
        uncertain = _latest_uncertain_results()
        if running or uncertain:
            _log(f"NO_ACTION running={running} uncertain={uncertain}")
            return 0
        state = "MISSING"
        if PROGRESS_PATH.exists():
            progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
            state = str(progress.get("state", ""))
        log_tail = ""
        if GLOBAL_LOG_PATH.exists():
            log_tail = GLOBAL_LOG_PATH.read_text(encoding="utf-8", errors="replace")[-20000:]
        try:
            pending = _discover_pending_periods()
        except Exception as exc:  # noqa: BLE001 - watchdog must fail closed
            _log(f"PENDING_CHECK_FAILED {type(exc).__name__}: {str(exc)[:300]}")
            return 0
        if not should_start(running, state, uncertain, log_tail, len(pending)):
            _log(
                f"NO_ACTION running={running} state={state} uncertain={uncertain} "
                f"pending_periods={len(pending)} pending_rows={sum(pending.values())}"
            )
            return 0
        session = f"soma-audit-watchdog-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
        command = (
            f"cd {PROJECT_ROOT} && {PROJECT_ROOT / '.venv/bin/python'} "
            f"{PROJECT_ROOT / 'workflows/soma_audit_all_periods_cli.py'} "
            f">> {GLOBAL_LOG_PATH} 2>&1"
        )
        result = subprocess.run(build_tmux_args(session, command), check=False)
        _log(
            f"START session={session} returncode={result.returncode} "
            f"pending_periods={len(pending)} pending_rows={sum(pending.values())}"
        )
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
