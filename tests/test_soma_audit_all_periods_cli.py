from pathlib import Path

from workflows.soma_audit_all_periods_cli import build_period_command, run_periods


def test_build_period_command_uses_existing_monthly_cli_with_apply_confirmation():
    command = build_period_command("python", Path("batch.py"), "09/2026")
    assert command == [
        "python",
        "batch.py",
        "--periodo",
        "09/2026",
        "--apply",
        "--confirm",
        "APPLY-SOMA-BATCH",
    ]


def test_run_periods_is_sequential_and_stops_on_systemic_failure():
    calls = []

    def runner(period):
        calls.append(period)
        return 1 if period == "02/2026" else 0

    completed, failed = run_periods(["01/2026", "02/2026", "03/2026"], runner)
    assert completed == ["01/2026"]
    assert failed == "02/2026"
    assert calls == ["01/2026", "02/2026"]
