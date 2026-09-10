from types import SimpleNamespace

import pytest

from domain.models import ContaOrdemRow, TipoMovimento
from services.soma_api_service import SomaApiService


class FakeResponse:
    status_code = 200
    text = "ok"


class FakeHttp:
    def __init__(self, search_html=""):
        self.search_html = search_html
        self.posts = []

    def post(self, url, data=None, **kwargs):
        self.posts.append((url, data, kwargs))
        return FakeResponse()

    def post_ajax(self, url, data=None, **kwargs):
        return SimpleNamespace(status_code=200, text=self.search_html)


def make_service(search_html=""):
    settings = SimpleNamespace(site_base_url="https://example.invalid/", institution_id="270", user_job_id="JOB")
    service = SomaApiService(settings, FakeHttp(search_html))
    service._loaded_catalogs = True
    service._confirmation_attempts = 1
    service._plano_contas_map = {"plano": "10"}
    service._centro_custo_map = {"centro": "20"}
    service._caixas_map = {"caixa": "30"}
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
    outcome = make_service().criar_entrada(make_row())
    assert outcome.success is False
    assert outcome.doc_id == ""
    assert "não foi confirmado" in outcome.error_message


def test_document_lookup_rejects_ambiguous_results():
    row = "<tr><td>{}</td><td>Entrada</td><td>OFERTA N001</td><td>15,00</td><td>10/09/2026</td></tr>"
    service = make_service(row.format("50100") + row.format("50101"))
    assert service._find_doc_id("1", "OFERTA N001", "15,00", "10/09/2026") is None


def test_document_lookup_requires_exact_description():
    html = "<tr><td>50100</td><td>Entrada</td><td>OUTRA OFERTA</td><td>15,00</td><td>10/09/2026</td></tr>"
    service = make_service(html)
    assert service._find_doc_id("1", "OFERTA N001", "15,00", "10/09/2026") is None
