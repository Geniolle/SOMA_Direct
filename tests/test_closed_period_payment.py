from types import SimpleNamespace

from domain.models import SomaSearchResult
from services.audit_service import AuditService


class Response:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._payload


class ClosedPeriodHttp:
    def __init__(self):
        self.payment_payloads = []

    def get(self, url):
        return Response(text=(
            '<input name="fluxo_valor" value="4,00">'
            '<select name="id_caixa"><option value="1226">CAIXA</option></select>'
        ))

    def post(self, url, data=None):
        if url.endswith("pagamentos.php"):
            self.payment_payloads.append(dict(data))
            status = 5 if len(self.payment_payloads) == 1 else 1
            return Response({"status": status})
        return Response({"status": 1})


def test_closed_month_retries_payment_in_current_open_period(monkeypatch):
    http = ClosedPeriodHttp()
    settings = SimpleNamespace(site_base_url="https://example.invalid/")
    audit = AuditService(settings, http, sheets=None)
    monkeypatch.setattr(
        audit,
        "search_by_codigo",
        lambda doc: SomaSearchResult(doc, "Saída", "TESTE", "4,00", "01/01/2024", "PAGO", "SIM"),
    )
    assert audit.insert_soma_payment("5500463", "01/01/2024", "4,00", "CAIXA", "TRANSFERÊNCIA")
    assert len(http.payment_payloads) == 2
    assert http.payment_payloads[0]["data_pagamento"] == "01/01/2024"
    assert http.payment_payloads[1]["data_pagamento"] != "01/01/2024"
