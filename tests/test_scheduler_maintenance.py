from agendador import run_cycle


class FakeOrchestrator:
    def __init__(self):
        self.maintenance_calls = 0

    def run_pending(self):
        return []

    def reconcile_scheduled_descriptions(self):
        self.maintenance_calls += 1
        return {"resolved": 0, "unresolved": 0}


def test_regular_cycle_does_not_run_heavy_maintenance():
    orchestrator = FakeOrchestrator()
    run_cycle(orchestrator, run_maintenance=False)
    assert orchestrator.maintenance_calls == 0


def test_scheduled_cycle_runs_description_maintenance():
    orchestrator = FakeOrchestrator()
    run_cycle(orchestrator, run_maintenance=True)
    assert orchestrator.maintenance_calls == 1
