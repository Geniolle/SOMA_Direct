"""Regressões dos três fluxos de INPUT (Entrada, Saída, Transferência)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.auth import SomaAuthenticator
from domain.models import ContaOrdemRow, OperationOutcome, TipoMovimento
from services.soma_api_service import SomaApiService
from workflows.orchestrator import DirectOrchestrator


def make_row(**overrides):
    values = {
        "row_number": 7,
        "data_mov": "03/09/2026",
        "descricao": "Descrição",
        "descricao_soma": "DESCRIÇÃO N001",
        "importancia": "115,00",
        "doc_soma": "",
        "tipo": TipoMovimento.ENTRADA,
        "plano_conta": "PLANO",
        "centro_custo": "CENTRO",
        "forma_pagamento": "DINHEIRO",
        "caixa": "Caixa Económica Montepio Geral - CC",
        "caixa_saida": "Caixa Diário",
        "processo": "T_EXTRATO",
        "id_interno": "EXT001",
    }
    values.update(overrides)
    return ContaOrdemRow(**values)


def transfer_row_html(transfer_id, origem, valor, destino, data):
    return (
        f'<tr><td><a class="btn btn-danger bnt_excluir" id="{transfer_id}"></a></td>'
        f"<td>{origem}</td><td>€ {valor}</td><td>{destino}</td><td>€ {valor}</td><td>{data}</td></tr>"
    )


def make_orchestrator(api):
    orch = DirectOrchestrator.__new__(DirectOrchestrator)
    orch.api = api
    orch.sheets = MagicMock()
    orch.audit_service = MagicMock()
    orch.audit_service.search_by_descricao.return_value = []
    return orch


def make_api():
    api = MagicMock()
    for name in ("criar_entrada", "criar_saida", "criar_transferencia"):
        getattr(api, name).return_value = OperationOutcome(False, "", "x", 7, 0, error_message="stop")
    api.find_transfers.return_value = ([], [])
    return api


# --- Dispatch ---------------------------------------------------------------

@pytest.mark.parametrize(
    "tipo, expected",
    [
        (TipoMovimento.ENTRADA, "criar_entrada"),
        (TipoMovimento.SAIDA, "criar_saida"),
        (TipoMovimento.TRANSFERENCIA, "criar_transferencia"),
    ],
)
def test_each_type_calls_only_its_creation_api(tipo, expected):
    api = make_api()
    orch = make_orchestrator(api)

    orch._process_claimed_row(make_row(tipo=tipo))

    for name in ("criar_entrada", "criar_saida", "criar_transferencia"):
        assert getattr(api, name).call_count == (1 if name == expected else 0)


@pytest.mark.parametrize("tipo", [TipoMovimento.CARTAO, TipoMovimento.MVV, TipoMovimento.OUTRO])
def test_non_processable_types_never_reach_creation(tipo):
    api = make_api()
    orch = make_orchestrator(api)

    outcome = orch._process_claimed_row(make_row(tipo=tipo))

    assert not outcome.success
    api.criar_entrada.assert_not_called()
    api.criar_saida.assert_not_called()
    api.criar_transferencia.assert_not_called()


# --- Transferências: guarda anti-duplicado -----------------------------------

def make_soma_api(pages):
    """SomaApiService com HTTP falso: ``pages`` é uma lista de respostas da pesquisa."""
    http = MagicMock()
    responses = iter(pages)
    http.post_ajax.side_effect = lambda *a, **k: SimpleNamespace(status_code=200, text=next(responses))
    http.post.return_value = SimpleNamespace(status_code=200, text="<html><script>x.onerror=1</script>ok</html>", url="u")
    settings = SimpleNamespace(site_base_url="https://example.test/IVV", institution_id="1", user_job_id="J")
    api = SomaApiService(settings, http)
    api._confirmation_attempts = 2
    api.resolve_caixa_id = lambda name: "1"
    return api


def test_unrelated_transfer_is_not_reported_as_existing():
    # Bug anterior: sem correspondência, devolvia o primeiro ID da tabela.
    page = transfer_row_html("999", "Caixa Diário", "20,00", "Caixa Económica Montepio Geral - CC", "03/09/2026")
    api = make_soma_api([f"<table>{page}</table>"])

    same_amount, exact = api.find_transfers(make_row(tipo=TipoMovimento.TRANSFERENCIA))

    assert same_amount == [] and exact == []


def test_amount_is_not_matched_by_substring():
    page = transfer_row_html("998", "Caixa Diário", "1115,00", "Caixa Económica Montepio Geral - CC", "03/09/2026")
    api = make_soma_api([f"<table>{page}</table>"])

    same_amount, _ = api.find_transfers(make_row(tipo=TipoMovimento.TRANSFERENCIA))

    assert same_amount == []


def test_existing_exact_transfer_is_recovered_without_creation():
    api = make_api()
    transfer = SimpleNamespace(transfer_id="290734")
    api.find_transfers.return_value = ([transfer], [transfer])
    orch = make_orchestrator(api)

    outcome = orch._process_claimed_row(make_row(tipo=TipoMovimento.TRANSFERENCIA))

    assert outcome.success and outcome.doc_id == "TRF_290734"
    api.criar_transferencia.assert_not_called()
    assert orch.sheets.mark_row_completed.call_args.kwargs["doc_id"] == "TRF_290734"


def test_same_date_amount_with_other_caixas_blocks_creation():
    api = make_api()
    api.find_transfers.return_value = ([SimpleNamespace(transfer_id="1")], [])
    orch = make_orchestrator(api)

    outcome = orch._process_claimed_row(make_row(tipo=TipoMovimento.TRANSFERENCIA))

    assert not outcome.success
    api.criar_transferencia.assert_not_called()
    orch.sheets.mark_row_validation_error.assert_called_once()


def test_created_transfer_is_identified_by_new_id():
    row = make_row(tipo=TipoMovimento.TRANSFERENCIA)
    old = transfer_row_html("100", "Caixa Diário", "5,00", "Caixa Económica Montepio Geral - CC", "03/09/2026")
    new = transfer_row_html("101", "Caixa Diário", "115,00", "Caixa Económica Montepio Geral - CC", "03/09/2026")
    api = make_soma_api([f"<table>{old}</table>", f"<table>{old}{new}</table>"])

    outcome = api.criar_transferencia(row)

    assert outcome.success
    assert outcome.doc_id == "TRF_101"


def test_created_transfer_not_found_is_failure():
    row = make_row(tipo=TipoMovimento.TRANSFERENCIA)
    api = make_soma_api(["<table></table>"] * 3)

    outcome = api.criar_transferencia(row)

    assert not outcome.success
    assert "não foi localizada" in outcome.error_message


# --- Deteção de erro na resposta do SOMA -------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        "<html><script>console.error('x')</script><body>OK</body></html>",
        "<img onerror='f()'><div>Gravado</div>",
        "<div class='error-page'>salvo</div>",
    ],
)
def test_html_with_error_words_in_code_is_not_a_failure(body):
    assert SomaApiService._http_error(SimpleNamespace(status_code=200, text=body)) is None


@pytest.mark.parametrize("body", ["<div>Erro ao gravar</div>", "Não foi possível gravar", "Acesso negado"])
def test_functional_error_messages_are_detected(body):
    assert SomaApiService._http_error(SimpleNamespace(status_code=200, text=body))


# --- Sessão ------------------------------------------------------------------

def test_forced_login_discards_expired_session_cookie():
    http = MagicMock()
    http.post_ajax.side_effect = [SimpleNamespace(text="1"), SimpleNamespace(text="1")]
    http.get.return_value = SimpleNamespace(text="Entradas/Saídas")
    settings = SimpleNamespace(site_user="u", site_password="p", site_base_url="https://example.test/IVV")
    auth = SomaAuthenticator(settings, http)
    auth.is_authenticated = True

    assert auth.login(force=True)
    http.session.cookies.clear.assert_called_once()
    assert http.post_ajax.call_count == 2


def test_each_cycle_renews_soma_session():
    orch = DirectOrchestrator.__new__(DirectOrchestrator)
    orch.auth = MagicMock()
    orch.api = MagicMock()

    orch.initialize()

    orch.auth.login.assert_called_once_with(force=True)


# --- Leitura da CONTAORDEM ---------------------------------------------------

def test_accented_caixa_saida_header_is_read():
    row = ContaOrdemRow.from_dict(2, {"TIPO": "Transferência", "CAIXA SAÍDA": "Caixa Diário"})
    assert row.caixa_saida == "Caixa Diário"
