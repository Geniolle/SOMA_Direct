from types import SimpleNamespace

import pytest

from domain.models import ContaOrdemRow, TipoMovimento
from services.soma_api_service import SomaApiService


class FakeResponse:
    status_code = 200
    text = "ok"


class FakeHttp:
    def __init__(self, search_html="", save_text="ok"):
        self.search_html = search_html
        self.save_text = save_text
        self.posts = []

    def post(self, url, data=None, **kwargs):
        self.posts.append((url, data, kwargs))
        return FakeResponse()

    def post_ajax(self, url, data=None, **kwargs):
        if "sys/app/" in url:
            self.posts.append((url, data, kwargs))
            return SimpleNamespace(status_code=200, text=self.save_text, url=url)
        return SimpleNamespace(status_code=200, text=self.search_html)


def make_service(search_html="", save_text="ok"):
    settings = SimpleNamespace(site_base_url="https://example.invalid/", institution_id="270", user_job_id="JOB")
    service = SomaApiService(settings, FakeHttp(search_html, save_text))
    service._loaded_catalogs = True
    service._confirmation_attempts = 1
    service._plano_contas_map = {"plano": "10"}
    service._centro_custo_map = {"centro": "20"}
    service._caixas_map = {"caixa": "30"}
    service._formas_pagamento_map = {"dinheiro": "0", "deposito": "1", "transferencia bancaria": "3"}
    return service


def make_row():
    return ContaOrdemRow(
        row_number=2,
        data_mov="10/09/2026",
        descricao="OFERTA",
        descricao_soma="OFERTA N001",
        importancia="15,00",
        doc_soma="",
        tipo=TipoMovimento.ENTRADA,
        plano_conta="PLANO",
        centro_custo="CENTRO",
        caixa="CAIXA",
        forma_pagamento="DINHEIRO",
    )


def test_unknown_catalog_value_fails_closed():
    service = make_service()
    with pytest.raises(ValueError, match="Plano de conta não encontrado"):
        service.resolve_plano_id("OUTRO")


def test_dynamic_cost_center_catalog_is_parsed():
    service = make_service()
    service._centro_custo_map = {service._norm("20.10.01 - ALUGUER"): "987"}
    assert service.resolve_centro_custo_id("20.10.01 - ALUGUER") == "987"


def test_creation_without_confirmed_document_is_failure():
    # Resposta sem JSON {status: 1}: o SOMA não confirmou a gravação.
    outcome = make_service().criar_entrada(make_row())
    assert outcome.success is False
    assert outcome.doc_id == ""
    assert "Resposta inválida" in outcome.error_message


def test_entrada_posts_to_official_endpoint_and_uses_returned_doc():
    service = make_service(save_text='{"status": 1, "id": "5540001"}')
    outcome = service.criar_entrada(make_row())
    url, payload, _ = service.http.posts[-1]
    assert url.endswith("sys/app/entradas_saidas.php")
    assert payload["forma_pagamento"] == "0"  # DINHEIRO pelo catálogo, não "1" fixo
    assert payload["tipo"] == "1"
    assert outcome.success and outcome.doc_id == "5540001"


def test_saida_rejected_by_soma_is_failure():
    service = make_service(save_text='{"status": 2}')
    row = make_row()
    row.tipo = TipoMovimento.SAIDA
    outcome = service.criar_saida(row)
    assert not outcome.success
    assert "status=2" in outcome.error_message


def test_status_one_without_document_is_not_success():
    outcome = make_service(save_text='{"status": 1}').criar_entrada(make_row())
    assert not outcome.success
    assert "não foi identificado" in outcome.error_message


def test_document_lookup_rejects_ambiguous_results():
    row = "<tr><td>{}</td><td>Entrada</td><td>OFERTA N001</td><td>15,00</td><td>10/09/2026</td></tr>"
    service = make_service(row.format("50100") + row.format("50101"))
    assert service._find_doc_id("1", "OFERTA N001", "15,00", "10/09/2026") is None


def test_document_lookup_requires_exact_description():
    html = "<tr><td>50100</td><td>Entrada</td><td>OUTRA OFERTA</td><td>15,00</td><td>10/09/2026</td></tr>"
    service = make_service(html)
    assert service._find_doc_id("1", "OFERTA N001", "15,00", "10/09/2026") is None
