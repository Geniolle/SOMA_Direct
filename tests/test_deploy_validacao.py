"""Revisão final de deploy: barreira de escrita, pausa, inicialização, reinício e rollback."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.http_session import ResilientSession
from core.operation_ledger import LedgerError, OperationLedger, RESULTADO_DESCONHECIDO
from core.write_gate import (
    EscritaFinanceiraBloqueada,
    eh_escrita_financeira,
    escrita_autorizada_pelo_ledger,
    verificar_escrita,
)
from domain.models import TipoMovimento
from tests.test_input_flows import make_row
from tests.test_operation_ledger import _ok, _orch
from workflows.orchestrator import DirectOrchestrator

SOMA = "https://verbodavida.info/IVV/"


def _sessao_falsa():
    sessao = ResilientSession(timeout=1, verify_tls=False)
    sessao.session = MagicMock()
    sessao.session.post.return_value = MagicMock(status_code=200, text="{}")
    return sessao


@pytest.fixture
def pausa(tmp_path, monkeypatch):
    """Diretório de dados isolado: o ledger e a pausa ficam neste diretório."""
    monkeypatch.setenv("SOMA_LEDGER_PATH", str(tmp_path / "dados" / "ledger.sqlite3"))
    return tmp_path / "dados" / "PAUSA_FINANCEIRA"


# --- Classificação e barreira de escrita -------------------------------------------

@pytest.mark.parametrize("url,data,esperado", [
    (SOMA + "sys/app/entradas_saidas.php", {}, True),
    (SOMA + "sys/app/transferencias_caixas.php", {"id": "1", "excluir": "1"}, True),
    (SOMA + "sys/app/baixas.php", {}, True),
    (SOMA + "?mod=app&exec=entradas_saidas&faz=dados", {}, True),
    (SOMA + "sys/post/buscarEntradasSaidas.php", {"pesquisa": "x"}, False),
    (SOMA + "sys/post/buscarTransferenciasCaixas.php", {}, False),
    ("https://verbodavida.info/apps/sys/buscaUser.php", {}, False),
    ("https://verbodavida.info/apps/sys/redirecionar.php", {}, False),
])
def test_classificacao_de_escrita(url, data, esperado):
    assert eh_escrita_financeira(url, data) is esperado


def test_post_de_escrita_sem_autorizacao_nunca_sai_da_maquina(pausa):
    sessao = _sessao_falsa()
    with pytest.raises(EscritaFinanceiraBloqueada, match="sem autorização"):
        sessao.post(SOMA + "sys/app/entradas_saidas.php", data={"valor": "1"})
    sessao.session.post.assert_not_called()


def test_leituras_e_login_nao_sao_bloqueados(pausa):
    pausa.parent.mkdir(parents=True, exist_ok=True)
    pausa.write_text("pausa", encoding="utf-8")  # mesmo com pausa, leituras continuam
    sessao = _sessao_falsa()
    sessao.post(SOMA + "sys/post/buscarEntradasSaidas.php", data={"pesquisa": "x"})
    sessao.post("https://verbodavida.info/apps/sys/buscaUser.php", data={})
    assert sessao.session.post.call_count == 2


def test_escrita_autorizada_passa_sem_pausa(pausa):
    sessao = _sessao_falsa()
    with escrita_autorizada_pelo_ledger():
        sessao.post(SOMA + "sys/app/entradas_saidas.php", data={"valor": "1"})
    sessao.session.post.assert_called_once()


def test_autorizacao_nao_persiste_fora_do_bloco(pausa):
    sessao = _sessao_falsa()
    with escrita_autorizada_pelo_ledger():
        pass
    with pytest.raises(EscritaFinanceiraBloqueada):
        sessao.post(SOMA + "sys/app/entradas_saidas.php", data={})


def test_pausa_bloqueia_escrita_mesmo_autorizada(pausa):
    pausa.parent.mkdir(parents=True, exist_ok=True)
    pausa.write_text("rollback", encoding="utf-8")
    sessao = _sessao_falsa()
    with escrita_autorizada_pelo_ledger():
        with pytest.raises(EscritaFinanceiraBloqueada, match="pausa"):
            sessao.post(SOMA + "sys/app/entradas_saidas.php", data={})
    sessao.session.post.assert_not_called()


def test_script_ou_ronda_que_exclui_transferencia_e_bloqueado(pausa):
    sessao = _sessao_falsa()
    with pytest.raises(EscritaFinanceiraBloqueada):
        sessao.post(SOMA + "sys/app/transferencias_caixas.php", data={"id": "304727", "excluir": "1"})
    sessao.session.post.assert_not_called()


# --- Arranque do novo código -------------------------------------------------------

def _construir_orquestrador(ledger_path):
    settings = SimpleNamespace(timeout_seconds=1, verify_tls=False)
    with patch("workflows.orchestrator.ResilientSession"), \
         patch("workflows.orchestrator.SomaAuthenticator"), \
         patch("workflows.orchestrator.SomaApiService"), \
         patch("workflows.orchestrator.DuplicateChecker"), \
         patch("workflows.orchestrator.GoogleSheetsService"), \
         patch("workflows.orchestrator.AuditService"):
        return DirectOrchestrator(settings=settings, ledger_path=ledger_path)


def test_falha_na_inicializacao_do_ledger_impede_arranque(tmp_path):
    with pytest.raises(LedgerError, match="ausente"):
        _construir_orquestrador(tmp_path / "nao_inicializado.sqlite3")


def test_arranque_com_pausa_ativa_nao_faz_post(pausa):
    ledger_path = pausa.parent / "ledger.sqlite3"
    OperationLedger.inicializar(ledger_path).close()
    pausa.write_text("pausa antes do arranque", encoding="utf-8")
    orch = _construir_orquestrador(ledger_path)
    orch._validate_launch_row = lambda row: None
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success and "pausados" in out.error_message
    orch.api.criar_saida.assert_not_called()
    orch.sheets.claim_row.assert_not_called()


# --- Reinício após falha da Sheet ----------------------------------------------------

def test_reinicio_apos_falha_da_sheet_nao_repete_post(tmp_path):
    ledger_path = tmp_path / "dados" / "ledger.sqlite3"
    orch1 = _orch(ledger_path, api_outcome=_ok())
    orch1.sheets.mark_row_completed.side_effect = RuntimeError("Sheets 503")
    orch1.sheets.mark_row_unknown.side_effect = RuntimeError("Sheets 503")
    orch1.process_row(make_row(tipo=TipoMovimento.SAIDA))
    orch1.ledger.close()
    orch2 = _orch(ledger_path, api_outcome=_ok())
    orch2.process_row(make_row(tipo=TipoMovimento.SAIDA))
    orch2.api.criar_saida.assert_not_called()


# --- Rollback com operação desconhecida ----------------------------------------------

def test_rollback_com_desconhecida_bloqueia_e_preserva_registo(tmp_path):
    ledger_path = tmp_path / "dados" / "ledger.sqlite3"
    led = OperationLedger.inicializar(ledger_path)
    led.registar_envio("T_EXTRATO|EXT_RB2")
    led.registar_resultado("T_EXTRATO|EXT_RB2", RESULTADO_DESCONHECIDO, detalhe="timeout")
    led.close()
    orch = _orch(ledger_path, api_outcome=_ok())
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA, processo="T_EXTRATO", id_interno="EXT_RB2"))
    assert not out.success
    orch.api.criar_saida.assert_not_called()
    assert orch.ledger.obter("T_EXTRATO|EXT_RB2").estado == RESULTADO_DESCONHECIDO
