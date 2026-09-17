from __future__ import annotations

import subprocess
import sys

from config.paths import PROJECT_ROOT
from workflows import reconciliation_cli
from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


class FakePlan:
    @staticmethod
    def summary():
        return {"contaordem": 1, "origem": 2, "soma": 3, "total": 6}


class FakeOrchestrator:
    calls = []

    def run(self, dry_run=True):
        self.calls.append(dry_run)
        return FakePlan()


def test_orchestrator_import_is_public():
    assert ReconciliationOrchestrator.__name__ == "ReconciliationOrchestrator"


def test_orchestrator_supports_legacy_direct_file_execution(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "workflows" / "reconciliation_orchestrator.py"),
            "--help",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--apply" in result.stdout


def test_cli_defaults_to_dry_run(monkeypatch):
    FakeOrchestrator.calls.clear()
    monkeypatch.setattr(reconciliation_cli, "ReconciliationOrchestrator", FakeOrchestrator)
    assert reconciliation_cli.main([]) == 0
    assert FakeOrchestrator.calls == [True]


def test_cli_apply_is_explicit(monkeypatch):
    FakeOrchestrator.calls.clear()
    monkeypatch.setattr(reconciliation_cli, "ReconciliationOrchestrator", FakeOrchestrator)
    assert reconciliation_cli.main(["--apply"]) == 0
    assert FakeOrchestrator.calls == [False]
