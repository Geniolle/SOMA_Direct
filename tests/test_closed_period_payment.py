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


def test_closed_month_does_not_retry_payment_in_current_open_period(monkeypatch):
    http = ClosedPeriodHttp()
    settings = SimpleNamespace(site_base_url="https://example.invalid/")
    audit = AuditService(settings, http, sheets=None)
    monkeypatch.setattr(
        audit,
        "search_by_codigo",
        lambda doc: SomaSearchResult(doc, "Saída", "TESTE", "4,00", "01/01/2024", "EM ABERTO", ""),
    )
    assert not audit.insert_soma_payment("5500463", "01/01/2024", "4,00", "CAIXA", "TRANSFERÊNCIA")
    assert len(http.payment_payloads) == 1
    assert http.payment_payloads[0]["data_pagamento"] == "01/01/2024"


class PaymentHttp:
    def __init__(self, pages, payment_status=1, baixa_status=1):
        self.pages = list(pages)
        self.last_page = self.pages[-1]
        self.payment_status = payment_status
        self.baixa_status = baixa_status
        self.posts = []

    def get(self, url):
        if self.pages:
            self.last_page = self.pages.pop(0)
        return Response(text=self.last_page)

    def post(self, url, data=None):
        self.posts.append((url, dict(data or {})))
        if url.endswith("pagamentos.php"):
            return Response({"status": self.payment_status})
        return Response({"status": self.baixa_status})


def result(status="EM ABERTO", baixa=""):
    return SomaSearchResult(
        "5629124",
        "Saída",
        "ENERGIA ELÉTRICA N001",
        "244,42",
        "26/10/2026",
        status,
        baixa,
    )


def page(payment_ids=(), pending_baixa_ids=()):
    payments = "".join(
        f'<input id="{payment_id}" class="pagamentos_check">'
        for payment_id in payment_ids
    )
    baixas = "".join(
        f'<button class="inst_baixa" id="{payment_id}"></button>'
        for payment_id in pending_baixa_ids
    )
    return (
        '<input name="fluxo_valor" value="244,42">'
        '<select name="id_caixa"><option value="1226">MONTEPIO</option></select>'
        + payments
        + baixas
    )


def service(http, monkeypatch, search_results):
    audit = AuditService(
        SimpleNamespace(site_base_url="https://example.invalid/"),
        http,
        sheets=None,
    )
    results = iter(search_results)
    monkeypatch.setattr(audit, "search_by_codigo", lambda doc: next(results))
    return audit


def payment_posts(http):
    return [item for item in http.posts if item[0].endswith("pagamentos.php")]


def baixa_posts(http):
    return [item for item in http.posts if item[0].endswith("baixas.php")]


def test_without_payment_creates_one_then_settles_it(monkeypatch):
    http = PaymentHttp([page(), page([41], [41])])
    audit = service(http, monkeypatch, [result(), result(), result("PAGO", "SIM")])

    assert audit.insert_soma_payment(
        "5629124", "26/10/2026", "244,42", "MONTEPIO", "TRANSFERÊNCIA"
    )

    assert len(payment_posts(http)) == 1
    assert payment_posts(http)[0][1]["data_pagamento"] == "26/10/2026"
    assert len(baixa_posts(http)) == 1
    assert baixa_posts(http)[0][1]["id_pagamento_baixa"] == "41"
    assert baixa_posts(http)[0][1]["data_baixa"] == "26/10/2026"


def test_created_payment_with_automatic_baixa_does_not_settle_again(monkeypatch):
    http = PaymentHttp([page(), page([41])])
    audit = service(http, monkeypatch, [result(), result("PAGO", "SIM")])

    assert audit.insert_soma_payment(
        "5629124", "26/10/2026", "244,42", "MONTEPIO", "TRANSFERÊNCIA"
    )

    assert len(payment_posts(http)) == 1
    assert baixa_posts(http) == []


def test_existing_payment_with_pending_baixa_only_settles(monkeypatch):
    http = PaymentHttp([page([41], [41])])
    audit = service(http, monkeypatch, [result(), result("PAGO", "SIM")])

    assert audit.insert_soma_payment(
        "5629124", "26/10/2026", "244,42", "MONTEPIO", "TRANSFERÊNCIA"
    )

    assert payment_posts(http) == []
    assert len(baixa_posts(http)) == 1


def test_completed_payment_does_nothing(monkeypatch):
    http = PaymentHttp([page([41])])
    audit = service(http, monkeypatch, [result("PAGO", "SIM")])

    assert audit.insert_soma_payment(
        "5629124", "26/10/2026", "244,42", "MONTEPIO", "TRANSFERÊNCIA"
    )

    assert http.posts == []


def test_multiple_payments_stop_without_mutation(monkeypatch):
    http = PaymentHttp([page([41, 42], [41])])
    audit = service(http, monkeypatch, [result()])

    assert not audit.insert_soma_payment(
        "5629124", "26/10/2026", "244,42", "MONTEPIO", "TRANSFERÊNCIA"
    )

    assert http.posts == []


def test_mismatched_payment_and_baixa_ids_stop_without_mutation(monkeypatch):
    http = PaymentHttp([page([41], [42])])
    audit = service(http, monkeypatch, [result()])

    assert not audit.insert_soma_payment(
        "5629124", "26/10/2026", "244,42", "MONTEPIO", "TRANSFERÊNCIA"
    )

    assert http.posts == []


def test_uncertain_payment_response_is_not_retried(monkeypatch):
    http = PaymentHttp([page()], payment_status=0)
    audit = service(http, monkeypatch, [result()])

    assert not audit.insert_soma_payment(
        "5629124", "26/10/2026", "244,42", "MONTEPIO", "TRANSFERÊNCIA"
    )

    assert len(payment_posts(http)) == 1
    assert baixa_posts(http) == []
