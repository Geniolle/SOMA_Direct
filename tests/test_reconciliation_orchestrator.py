from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


class FakeWorksheet:
    def __init__(self, values):
        self.values = values

    def get_all_values(self):
        return self.values


class FakeSpreadsheet:
    def __init__(self, worksheets):
        self.worksheets = worksheets

    def worksheet(self, name):
        return self.worksheets[name]


class FakeSheetsService:
    def __init__(self, contaordem, origins):
        self._ws = FakeWorksheet(contaordem)
        self._sh = FakeSpreadsheet(
            {name: FakeWorksheet(values) for name, values in origins.items()}
        )


def test_validate_origin_ids_stops_cross_check_when_duplicates_exist():
    sheets = FakeSheetsService(
        contaordem=[
            ["ID_INTERNO", "DOC. SOMA", "PROCESSO"],
            ["EXT001", "1000001", "T_EXTRATO"],
            ["EXT002", "1000002", "T_EXTRATO"],
            ["EXT002", "1000003", "T_EXTRATO"],
            ["EXT999", "1000004", "T_EXTRATO"],
        ],
        origins={
            "T_EXTRATO": [
                ["DESCRIÇÃO", "ID INTERNO"],
                ["A", "EXT001"],
                ["B", "EXT002"],
                ["C", "EXT003"],
                ["D", ""],
                ["E", "EXT004"],
                ["F", "EXT004"],
            ]
        },
    )
    orchestrator = ReconciliationOrchestrator.__new__(ReconciliationOrchestrator)
    orchestrator.sheets = sheets
    orchestrator._external_source_spreadsheet = None

    report = orchestrator.validate_origin_ids(("T_EXTRATO",))

    assert report.contaordem_ids == 3
    assert report.contaordem_duplicate_ids == 1
    assert report.summary() == {
        "sources": 1,
        "rows_checked": 6,
        "found": 0,
        "missing": 0,
        "blank": 0,
        "duplicate_in_source": 2,
        "duplicate_in_contaordem": 2,
        "contaordem_rows_checked": 4,
        "missing_in_source": 0,
        "source_errors": 0,
        "blocked_by_duplicates": 1,
    }
    reverse_items = [
        item
        for item in report.sources[0].items
        if item.direction == "CONTAORDEM_PARA_ORIGEM"
    ]
    assert {item.id_interno for item in reverse_items} == {"EXT002"}
    assert {item.status for item in reverse_items} == {"DUPLICADO_NA_CONTAORDEM"}
    assert reverse_items[-1].source_row is None


def test_validate_origin_ids_cross_checks_both_directions_without_duplicates():
    sheets = FakeSheetsService(
        contaordem=[
            ["ID_INTERNO", "PROCESSO"],
            ["EXT001", "T_EXTRATO"],
            ["EXT999", "T_EXTRATO"],
        ],
        origins={
            "T_EXTRATO": [
                ["ID_INTERNO"],
                ["EXT001"],
                ["EXT003"],
            ]
        },
    )
    orchestrator = ReconciliationOrchestrator.__new__(ReconciliationOrchestrator)
    orchestrator.sheets = sheets
    orchestrator._external_source_spreadsheet = None

    report = orchestrator.validate_origin_ids(("T_EXTRATO",))

    assert report.blocked_by_duplicates is False
    assert report.sources[0].found == 1
    assert report.sources[0].missing == 1
    assert report.sources[0].missing_in_source == 1


def test_validate_origin_ids_reports_unavailable_source():
    sheets = FakeSheetsService(
        contaordem=[["ID_INTERNO", "PROCESSO"], ["EXT001", "Financeiro"]],
        origins={},
    )
    orchestrator = ReconciliationOrchestrator.__new__(ReconciliationOrchestrator)
    orchestrator.sheets = sheets
    orchestrator._external_source_spreadsheet = None

    report = orchestrator.validate_origin_ids(("Financeiro",))

    assert report.summary()["source_errors"] == 1
    assert report.summary()["blocked_by_duplicates"] == 0
    assert report.sources[0].error
