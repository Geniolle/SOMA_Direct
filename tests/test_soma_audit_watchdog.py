import json
from contextlib import nullcontext
from pathlib import Path

import workflows.soma_audit_watchdog as watchdog
from workflows.soma_audit_watchdog import build_tmux_args, should_restart, should_start


def test_running_process_never_restarts():
    assert should_restart(True, "FAILED", 0, "Quota exceeded") is False


def test_known_read_quota_failure_without_uncertainty_restarts():
    assert should_restart(False, "FAILED", 0, "APIError: [429]: Quota exceeded for quota metric 'Read requests'") is True


def test_uncertain_financial_result_blocks_restart():
    assert should_restart(False, "FAILED", 1, "APIError: [429]: Quota exceeded for quota metric 'Read requests'") is False


def test_unknown_failure_blocks_restart():
    assert should_restart(False, "FAILED", 0, "Permission denied") is False


def test_completed_run_never_restarts():
    assert should_restart(False, "COMPLETED", 0, "") is False


def test_completed_run_with_pending_period_starts():
    assert should_start(False, "COMPLETED", 0, "", 3) is True


def test_completed_run_without_pending_period_stays_idle():
    assert should_start(False, "COMPLETED", 0, "", 0) is False


def test_running_process_blocks_pending_start():
    assert should_start(True, "COMPLETED", 0, "", 3) is False


def test_uncertain_result_blocks_pending_start():
    assert should_start(False, "COMPLETED", 1, "", 3) is False


def test_unknown_failure_does_not_start_only_because_work_is_pending():
    assert should_start(False, "FAILED", 0, "Permission denied", 3) is False


def test_pending_check_failure_is_logged_without_launch(tmp_path, monkeypatch):
    progress = tmp_path / "progress.json"
    progress.write_text(json.dumps({"state": "COMPLETED"}), encoding="utf-8")
    logs = []
    launches = []
    monkeypatch.setattr(watchdog, "PROGRESS_PATH", progress)
    monkeypatch.setattr(watchdog, "orchestrator_session", lambda path: nullcontext(True))
    monkeypatch.setattr(watchdog, "_process_running", lambda: False)
    monkeypatch.setattr(watchdog, "_latest_uncertain_results", lambda: 0)
    monkeypatch.setattr(watchdog, "_discover_pending_periods", lambda: (_ for _ in ()).throw(RuntimeError("quota")))
    monkeypatch.setattr(watchdog, "_log", logs.append)
    monkeypatch.setattr(watchdog.subprocess, "run", lambda *args, **kwargs: launches.append(args))

    assert watchdog.main() == 0
    assert logs == ["PENDING_CHECK_FAILED RuntimeError: quota"]
    assert launches == []


def test_tmux_runs_supervisor_through_explicit_shell():
    args = build_tmux_args("session", "echo ok >> log")
    assert args == ["tmux", "new-session", "-d", "-s", "session", "/bin/bash", "-lc", "echo ok >> log"]


def test_watchdog_service_preserves_detached_tmux_child():
    unit = Path("Servidor/soma-audit-watchdog.service").read_text(encoding="utf-8")
    assert "User=ubuntu" in unit
    assert "KillMode=process" in unit
