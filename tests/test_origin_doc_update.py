from types import SimpleNamespace

import pytest

from services.sheets_service import GoogleSheetsService


class FakeWorksheet:
    def __init__(self, values):
        self.values = values
        self.updates = []

    def get_all_values(self):
        return self.values

    def update(self, cell, values):
        self.updates.append((cell, values))

    def batch_update(self, updates):
        self.updates.append(("batch", updates))


class FakeSpreadsheet:
    def __init__(self, worksheets):
        self.worksheets = worksheets

    def worksheet(self, name):
        return self.worksheets[name]


def make_service(origin):
    service = GoogleSheetsService.__new__(GoogleSheetsService)
    service._sh = FakeSpreadsheet({"T_EXTRATO": origin})
    service._ws = FakeWorksheet([])
    service._gc = SimpleNamespace()
    service.settings = SimpleNamespace(user_job_id="JOB")
    service._headers_cache = ["DOC. SOMA", "STATUS", "AUDITORIA", "IDUSER", "TIMESTAMP"]
    return service


def test_completed_row_updates_origin_by_id_before_contaordem():
    origin = FakeWorksheet([
        ["DESCRIÇÃO", "ID_INTERNO", "DOC. SOMA"],
        ["Movimento", "EXT001", ""],
    ])
    service = make_service(origin)

    service.mark_row_completed(
        row_idx=8,
        doc_id="5500123",
        processo="T_EXTRATO",
        id_interno="EXT001",
    )

    assert origin.updates == [("C2", [["5500123"]])]
    assert service._ws.updates[0][0] == "batch"
    assert {item["range"]: item["values"] for item in service._ws.updates[0][1]}["A8"] == [["5500123"]]


def test_origin_update_rejects_duplicate_internal_id():
    origin = FakeWorksheet([
        ["ID INTERNO", "DOC. SOMA"],
        ["EXT001", ""],
        ["EXT001", ""],
    ])
    service = make_service(origin)

    with pytest.raises(ValueError, match="encontrado 2 vez"):
        service._update_origin_doc("T_EXTRATO", "EXT001", "5500123")
    assert origin.updates == []


def test_origin_update_overwrites_another_document():
    origin = FakeWorksheet([
        ["ID_INTERNO", "DOC. SOMA"],
        ["EXT001", "4400000"],
    ])
    service = make_service(origin)

    service._update_origin_doc("T_EXTRATO", "EXT001", "5500123")

    assert origin.updates == [("B2", [["5500123"]])]
