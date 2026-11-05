"""Idempotência das gravações financeiras: CONFIRMADO / NAO_CRIADO / RESULTADO_DESCONHECIDO."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.operation_ledger import OperationLedger
from core.operation_ledger import RESULTADO_DESCONHECIDO
from domain.models import ESTADO_DESCONHECIDO, ESTADO_NAO_CRIADO, OperationOutcome, TipoMovimento
from services.soma_api_service import SomaApiService
from workflows.orchestrator import DirectOrchestrator
from tests.test_input_flows import make_row


def _ok(doc="5654059"):
    return OperationOutcome(True, doc, "Saída", 7, 10, dados_doc="x")


def _api(text, status_code=200, find_doc=None):
    """Serviço com HTTP falso que devolve ``text`` a cada POST AJAX."""
    http = MagicMock()
    http.post_ajax.side_effect = lambda *a, **k: SimpleNamespace(status_code=status_code, text=text, url="")
    settings = SimpleNamespace(site_base_url="https://example.test/IVV", institution_id="1", user_job_id="J")
    api = SomaApiService(settings, http)
    api._confirmation_attempts = 1
    api.resolve_plano_id = lambda name: "1"
    api.resolve_centro_custo_id = lambda name: "1"
    api.resolve_caixa_id = lambda name: "1"
    api.resolve_forma_pagamento_id = lambda name: "1"
    api._wait_find_doc = lambda **kw: find_doc
    return api, http


# --- Classificação do resultado do POST -------------------------------------

def test_saida_confirmada_por_id_devolvido():
    api, http = _api('{"status": 1, "id": "5654059"}')
    out = api.criar_saida(make_row(tipo=TipoMovimento.SAIDA))
    assert out.success and out.doc_id == "5654059"
    assert http.post_ajax.call_count == 1


def test_saida_resposta_html_e_resultado_desconhecido_sem_retry():
    api, http = _api("<html>dashboard</html>")
    out = api.criar_saida(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success
    assert out.estado == ESTADO_DESCONHECIDO
    assert http.post_ajax.call_count == 1  # o serviço nunca repete o POST


def test_saida_status_1_sem_doc_identificado_e_desconhecido():
    api, _ = _api('{"status": 1}', find_doc=None)
    out = api.criar_saida(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success
    assert out.estado == ESTADO_DESCONHECIDO


def test_saida_recusada_com_status_explicito_e_nao_criada():
    api, _ = _api('{"status": 2}')
    out = api.criar_saida(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success
    assert out.estado == ESTADO_NAO_CRIADO


def test_transferencia_recusada_com_status_5_e_nao_criada():
    api, _ = _api('{"status": 5}')
    api.find_transfers = lambda row: ([], [])
    out = api.criar_transferencia(make_row(tipo=TipoMovimento.TRANSFERENCIA))
    assert not out.success
    assert out.estado == ESTADO_NAO_CRIADO


def test_transferencia_html_e_resultado_desconhecido():
    api, _ = _api("<html>dashboard</html>")
    api.find_transfers = lambda row: ([], [])
    out = api.criar_transferencia(make_row(tipo=TipoMovimento.TRANSFERENCIA))
    assert not out.success
    assert out.estado == ESTADO_DESCONHECIDO


# --- Seleção de pendentes ----------------------------------------------------

def test_linha_com_resultado_desconhecido_nao_e_pendente():
    row = make_row(tipo=TipoMovimento.SAIDA, doc_soma=ESTADO_DESCONHECIDO)
    assert DirectOrchestrator._is_pending(row, 3600) is False


def test_linha_em_erro_continua_pendente():
    row = make_row(tipo=TipoMovimento.SAIDA, doc_soma="EM ERRO")
    assert DirectOrchestrator._is_pending(row, 3600) is True


# --- Orquestrador: roteamento do estado -------------------------------------

def _orch(api_outcome=None, api_exc=None):
    orch = DirectOrchestrator.__new__(DirectOrchestrator)
    orch.sheets = MagicMock()
    orch.audit_service = MagicMock()
    orch.audit_service.search_by_descricao.return_value = []
    orch.api = MagicMock()
    orch.ledger = OperationLedger.inicializar(Path(tempfile.mkdtemp()) / "ledger.sqlite3")
    if api_exc is not None:
        orch.api.criar_saida.side_effect = api_exc
    else:
        orch.api.criar_saida.return_value = api_outcome
    return orch


def test_excecao_apos_envio_marca_linha_como_desconhecida_e_nao_em_erro():
    orch = _orch(api_exc=RuntimeError("ConnectionResetError"))
    out = orch._process_claimed_row(make_row(tipo=TipoMovimento.SAIDA))
    assert out.estado == ESTADO_DESCONHECIDO
    orch.sheets.mark_row_unknown.assert_called_once()
    orch.sheets.mark_row_failed.assert_not_called()


def test_erro_de_catalogo_na_preparacao_permite_nova_tentativa():
    orch = _orch(api_outcome=_ok())
    orch.api.validar_preparacao.side_effect = ValueError("Plano de conta não encontrado no SOMA")
    out = orch._process_claimed_row(make_row(tipo=TipoMovimento.SAIDA))
    assert out.estado == ESTADO_NAO_CRIADO
    orch.api.criar_saida.assert_not_called()
    orch.sheets.mark_row_failed.assert_called_once()
    orch.sheets.mark_row_unknown.assert_not_called()


def test_valueerror_depois_de_iniciar_post_e_desconhecido():
    orch = _orch(api_exc=ValueError("resposta inesperada"))
    out = orch._process_claimed_row(make_row(tipo=TipoMovimento.SAIDA))
    assert out.estado == ESTADO_DESCONHECIDO
    orch.sheets.mark_row_failed.assert_not_called()
    orch.sheets.mark_row_unknown.assert_called_once()
    assert orch.ledger.obter("T_EXTRATO|EXT001").estado == RESULTADO_DESCONHECIDO


def test_sucesso_sem_confirmacao_nao_fica_validado():
    ok = OperationOutcome(True, "5654059", "Saída", 7, 10, dados_doc="x")
    orch = _orch(api_outcome=ok)
    orch._confirm_and_settle = lambda row, doc: "Documento não localizado por código após o lançamento"
    out = orch._process_claimed_row(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success and out.estado == ESTADO_DESCONHECIDO
    orch.sheets.mark_row_unknown.assert_called_once()
    orch.sheets.mark_row_completed.assert_not_called()


def test_guarda_bloqueia_novo_post_em_linha_desconhecida():
    orch = _orch()
    orch._validate_launch_row = lambda row: None
    row = make_row(tipo=TipoMovimento.SAIDA, doc_soma=ESTADO_DESCONHECIDO)
    out = orch.process_row(row)
    assert not out.success and out.estado == ESTADO_DESCONHECIDO
    orch.sheets.claim_row.assert_not_called()
    orch.api.criar_saida.assert_not_called()
