from services.sheets_service import GoogleSheetsService


class FakeWorksheet:
    def __init__(self, values):
        self.values = values
        self.updates = []

    def row_values(self, _row):
        return self.values[0]

    def get_all_values(self):
        return [list(row) for row in self.values]

    def batch_update(self, updates):
        self.updates.extend(updates)


def test_harmonize_does_not_overwrite_description_with_numeric_doc():
    headers = ["DATA MOV.", "DESCRIÇÃO", "IMPORTÂNCIA", "DOC. SOMA", "TIPO", "DESCRIÇÃO SOMA"]
    worksheet = FakeWorksheet([
        headers,
        ["01/01/2026", "Oferta", "10,00", "123", "Entrada", "Oferta N007"],
        ["01/01/2026", "Oferta", "20,00", "Analisar", "Entrada", "Oferta"],
    ])
    service = GoogleSheetsService.__new__(GoogleSheetsService)
    service._ws = worksheet
    service._headers_cache = None

    result = service.harmonize_sequentials_and_duplicates(update_sheet=True)

    updated_ranges = {item["range"]: item["values"][0][0] for item in worksheet.updates}
    assert "F2" not in updated_ranges
    assert updated_ranges["F3"] == "Oferta N002"
    assert result["total_desc_adjusted"] == 1
