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


def test_cycle_calls_backfill_missing_links():
    from types import SimpleNamespace
    orchestrator = FakeOrchestrator()
    backfill_called = False

    def fake_backfill():
        nonlocal backfill_called
        backfill_called = True

    orchestrator.sheets = SimpleNamespace(backfill_missing_links=fake_backfill)
    run_cycle(orchestrator, run_maintenance=False)
    assert backfill_called is True


def test_sheets_service_backfill_missing_links():
    from services.sheets_service import GoogleSheetsService

    class MockWs:
        def __init__(self, rows):
            self.rows = rows
            self.batch_updates = []

        def get_all_values(self, **kwargs):
            return self.rows

        def batch_update(self, updates, **kwargs):
            self.batch_updates.extend(updates)

    service = GoogleSheetsService.__new__(GoogleSheetsService)
    service._ws = MockWs([
        ["DOC. SOMA", "LINK"],
        ["1234567", ""],
        ["1234567", '=HYPERLINK("https://verbodavida.info/IVV/?mod=ivv&exec=entradas_saidas_dados&ID=1234567";"ACESSAR SOMA")'],
        ["", ""],
        ["PENDENTE", ""],
        ["7654321", ""],
    ])

    updated = service.backfill_missing_links()
    assert updated == 2
    assert len(service._ws.batch_updates) == 2
    assert service._ws.batch_updates[0]["range"] == "B2"
    assert service._ws.batch_updates[1]["range"] == "B6"
