"""Ledger durável de operações financeiras: nenhum POST repetido sem reconciliação comprovada."""
import os
import threading
from unittest.mock import MagicMock

import pytest
import requests

from core.operation_ledger import (
    CONFIRMADO,
    ENVIANDO,
    NAO_CRIADO,
    RESULTADO_DESCONHECIDO,
    IdentificacaoInsuficiente,
    LedgerError,
    OperationLedger,
    chave_operacao,
)
from domain.models import ESTADO_DESCONHECIDO, OperationOutcome, TipoMovimento
from tests.test_input_flows import make_row
from workflows.orchestrator import DirectOrchestrator


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "state" / "ledger.sqlite3"


@pytest.fixture
def ledger(ledger_path):
    led = OperationLedger.inicializar(ledger_path)
    yield led
    led.close()


def _orch(ledger_path, api_outcome=None, api_exc=None):
    orch = DirectOrchestrator.__new__(DirectOrchestrator)
    orch.sheets = MagicMock()
    orch.audit_service = MagicMock()
    orch.audit_service.search_by_descricao.return_value = []
    orch.api = MagicMock()
    orch.ledger = OperationLedger.inicializar(ledger_path)
    if api_exc is not None:
        orch.api.criar_saida.side_effect = api_exc
    else:
        orch.api.criar_saida.return_value = api_outcome
    orch._validate_launch_row = lambda row: None
    orch.sheets.claim_row.return_value = "token"
    return orch


def _ok(doc="5654059"):
    return OperationOutcome(True, doc, "Saída", 7, 10, dados_doc="x")


# --- Estados básicos ---------------------------------------------------------

def test_registo_antes_do_envio_bloqueia_nova_intencao(ledger):
    ledger.registar_envio("EXT1")
    with pytest.raises(LedgerError):
        ledger.registar_envio("EXT1")


def test_resultado_sem_envio_previo_e_recusado(ledger):
    with pytest.raises(LedgerError):
        ledger.registar_resultado("EXT2", CONFIRMADO, doc_id="1")


def test_nao_criado_permite_nova_tentativa(ledger):
    ledger.registar_envio("EXT3")
    ledger.registar_resultado("EXT3", NAO_CRIADO, detalhe="recusa")
    ledger.registar_envio("EXT3")  # não levanta
    assert ledger.obter("EXT3").tentativas == 2


def test_confirmado_nunca_e_reenviado_nem_alterado(ledger):
    ledger.registar_envio("EXT4")
    ledger.registar_resultado("EXT4", CONFIRMADO, doc_id="5654059")
    with pytest.raises(LedgerError):
        ledger.registar_envio("EXT4")
    with pytest.raises(LedgerError):
        ledger.registar_resultado("EXT4", NAO_CRIADO)
    assert ledger.obter("EXT4").doc_id == "5654059"


def test_chave_estavel_por_processo_e_id_interno():
    assert chave_operacao("t_extrato", " EXT9 ") == "T_EXTRATO|EXT9"
    assert chave_operacao("SAIDAS", "EXT9") != chave_operacao("T_EXTRATO", "EXT9")


def test_sem_id_interno_ou_processo_nao_ha_chave():
    with pytest.raises(IdentificacaoInsuficiente):
        chave_operacao("T_EXTRATO", "")
    with pytest.raises(IdentificacaoInsuficiente):
        chave_operacao("", "EXT9")


# --- Reinício do serviço -----------------------------------------------------

def test_reinicio_com_envio_interrompido_continua_bloqueado(ledger_path):
    led = OperationLedger.inicializar(ledger_path)
    led.registar_envio("EXT5")  # processo morreu antes ou depois do POST
    led.close()
    led2 = OperationLedger.inicializar(ledger_path)
    try:
        assert led2.bloqueado("EXT5")
        with pytest.raises(LedgerError):
            led2.registar_envio("EXT5")
    finally:
        led2.close()


def test_interrupcao_antes_do_post_nao_chama_o_soma_apos_reinicio(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.api.criar_saida.side_effect = KeyboardInterrupt  # simula morte após registar ENVIANDO
    with pytest.raises(KeyboardInterrupt):
        orch._process_claimed_row(make_row(tipo=TipoMovimento.SAIDA))
    orch.api.criar_saida.side_effect = None

    orch2 = _orch(ledger_path, api_outcome=_ok())
    out = orch2.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success
    orch2.api.criar_saida.assert_not_called()


# --- Falhas de escrita ---------------------------------------------------------

def test_falha_da_sheet_apos_post_nao_grava_em_erro_nem_repete(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.sheets.mark_row_completed.side_effect = RuntimeError("Sheets 503")
    orch.sheets.mark_row_unknown.side_effect = RuntimeError("Sheets 503")
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    orch.sheets.mark_row_failed.assert_not_called()
    assert orch.ledger.obter("T_EXTRATO|EXT001").estado == CONFIRMADO

    orch.api.criar_saida.reset_mock()
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    orch.api.criar_saida.assert_not_called()


def test_falha_ao_persistir_resultado_bloqueia_e_nao_grava_em_erro(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    real = orch.ledger
    orch.ledger = MagicMock(wraps=real)
    orch.ledger.registar_resultado.side_effect = RuntimeError("disco cheio")
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success and out.estado == ESTADO_DESCONHECIDO
    orch.sheets.mark_row_failed.assert_not_called()
    orch.sheets.mark_row_unknown.assert_called_once()
    # Registo de envio ficou ENVIANDO: próxima tentativa é bloqueada.
    assert real.obter("T_EXTRATO|EXT001").estado == ENVIANDO
    orch.ledger = real
    orch.api.criar_saida.reset_mock()
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    orch.api.criar_saida.assert_not_called()


def test_erro_do_ledger_antes_do_envio_nao_envia(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.ledger = MagicMock()
    orch.ledger.obter.side_effect = RuntimeError("sqlite locked")
    orch.ledger.registar_envio.side_effect = RuntimeError("sqlite locked")
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success
    orch.api.criar_saida.assert_not_called()


# --- Respostas inesperadas ---------------------------------------------------

def test_resposta_html_registada_como_desconhecida(ledger_path):
    outcome = OperationOutcome(False, "", "Saída", 7, 0, error_message="Resposta inválida", estado=ESTADO_DESCONHECIDO)
    orch = _orch(ledger_path, api_outcome=outcome)
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert orch.ledger.obter("T_EXTRATO|EXT001").estado == RESULTADO_DESCONHECIDO
    orch.sheets.mark_row_failed.assert_not_called()


def test_timeout_apos_post_e_desconhecido(ledger_path):
    orch = _orch(ledger_path, api_exc=requests.exceptions.ReadTimeout("read timed out"))
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert orch.ledger.obter("T_EXTRATO|EXT001").estado == RESULTADO_DESCONHECIDO
    orch.sheets.mark_row_failed.assert_not_called()


# --- Concorrência e repetição ------------------------------------------------

def test_duas_instancias_concorrentes_so_uma_envia(ledger_path):
    OperationLedger.inicializar(ledger_path).close()  # cria o ficheiro
    barrier = threading.Barrier(2)
    resultados = []

    def tentar():
        led = OperationLedger.inicializar(ledger_path)
        barrier.wait()
        try:
            led.registar_envio("EXT_CONC")
            resultados.append("ok")
        except LedgerError:
            resultados.append("bloqueado")
        finally:
            led.close()

    threads = [threading.Thread(target=tentar) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(resultados) == ["bloqueado", "ok"]


def test_mesmo_id_interno_em_duas_linhas_envia_uma_vez(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA, row_number=7, id_interno="EXT777"))
    orch.api.criar_saida.reset_mock()
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA, row_number=8, id_interno="EXT777"))
    orch.api.criar_saida.assert_not_called()


# --- Reconciliação manual ----------------------------------------------------

def test_reconciliacao_com_motivo_libera_nova_tentativa(ledger):
    ledger.registar_envio("EXT6")
    ledger.registar_resultado("EXT6", RESULTADO_DESCONHECIDO, detalhe="html")
    with pytest.raises(LedgerError):
        ledger.reconciliar("EXT6", NAO_CRIADO, motivo="")
    ledger.reconciliar("EXT6", NAO_CRIADO, motivo="pesquisa no SOMA sem resultados")
    ledger.registar_envio("EXT6")  # libertado


def test_reconciliacao_confirmado_exige_doc(ledger):
    ledger.registar_envio("EXT7")
    with pytest.raises(LedgerError):
        ledger.reconciliar("EXT7", CONFIRMADO, motivo="visto no SOMA", doc_id="")
    ledger.reconciliar("EXT7", CONFIRMADO, motivo="visto no SOMA", doc_id="5654059")
    assert ledger.obter("EXT7").doc_id == "5654059"
    with pytest.raises(LedgerError):
        ledger.registar_envio("EXT7")


def test_nao_reconcilia_operacao_inexistente_ou_confirmada(ledger):
    with pytest.raises(LedgerError):
        ledger.reconciliar("EXT_NADA", NAO_CRIADO, motivo="x")
    ledger.registar_envio("EXT8")
    ledger.registar_resultado("EXT8", CONFIRMADO, doc_id="1")
    with pytest.raises(LedgerError):
        ledger.reconciliar("EXT8", NAO_CRIADO, motivo="x")


# --- Chave estável e identificação --------------------------------------------

def test_alteracao_de_posicao_da_linha_nao_repete_envio(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA, row_number=7, id_interno="EXT900"))
    orch.api.criar_saida.reset_mock()
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA, row_number=512, id_interno="EXT900"))
    orch.api.criar_saida.assert_not_called()


def test_id_interno_ausente_bloqueia_sem_tocar_no_soma(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA, id_interno=""))
    assert not out.success and "identificação insuficiente" in out.error_message
    orch.sheets.claim_row.assert_not_called()
    orch.api.criar_saida.assert_not_called()


def test_mesmo_id_interno_com_processo_diferente_e_operacao_distinta(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA, processo="T_EXTRATO", id_interno="EXT55"))
    orch.api.criar_saida.reset_mock()
    orch.process_row(make_row(tipo=TipoMovimento.SAIDA, processo="SAIDAS", id_interno="EXT55"))
    orch.api.criar_saida.assert_called_once()


def test_falha_de_rede_depois_do_envio_e_desconhecida(ledger_path):
    orch = _orch(ledger_path, api_exc=requests.exceptions.ConnectionError("reset by peer"))
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert out.estado == ESTADO_DESCONHECIDO
    assert orch.ledger.obter("T_EXTRATO|EXT001").estado == RESULTADO_DESCONHECIDO
    orch.sheets.mark_row_failed.assert_not_called()


# --- Persistência, rollback e falhas do ledger ----------------------------------

def test_rollback_do_codigo_preserva_o_ledger(tmp_path):
    app = tmp_path / "app_atual"
    app.mkdir()
    ledger_file = tmp_path / "dados" / "operacoes_financeiras.sqlite3"
    led = OperationLedger.inicializar(ledger_file)
    led.registar_envio("T_EXTRATO|EXT_ROLL")
    led.close()
    # Deploy/rollback: o diretório de código é substituído; o ledger fica fora dele.
    import shutil
    shutil.rmtree(app)
    (tmp_path / "app_novo").mkdir()
    led2 = OperationLedger(ledger_file)
    try:
        assert led2.obter("T_EXTRATO|EXT_ROLL").estado == ENVIANDO
    finally:
        led2.close()


def test_ledger_ausente_nao_e_criado_sem_inicializacao(tmp_path):
    with pytest.raises(LedgerError, match="ausente"):
        OperationLedger(tmp_path / "nao_existe.sqlite3")
    assert not (tmp_path / "nao_existe.sqlite3").exists()


def test_ledger_inacessivel_levanta_erro(tmp_path):
    bloqueio = tmp_path / "ficheiro"
    bloqueio.write_text("não é diretório")
    with pytest.raises(LedgerError):
        OperationLedger.inicializar(bloqueio / "ledger.sqlite3")


def test_ledger_corrompido_bloqueia_abertura(ledger_path):
    OperationLedger.inicializar(ledger_path).close()
    ledger_path.write_bytes(b"\x00garbage" * 2000)
    with pytest.raises(LedgerError):
        OperationLedger(ledger_path)


def test_ledger_corrompido_nao_permite_lancamento(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.ledger.close()
    ledger_path.write_bytes(b"\x00garbage" * 2000)
    with pytest.raises(LedgerError):
        OperationLedger(ledger_path)
    orch.api.criar_saida.assert_not_called()


@pytest.mark.skipif(os.name == "nt", reason="permissões POSIX")
def test_ledger_com_permissoes_inseguras_e_recusado(ledger_path):
    OperationLedger.inicializar(ledger_path).close()
    os.chmod(ledger_path, 0o644)
    with pytest.raises(LedgerError, match="permissões"):
        OperationLedger(ledger_path)


def test_ledger_nao_fica_dentro_da_arvore_do_codigo():
    from config.paths import PROJECT_ROOT
    from core.operation_ledger import resolve_ledger_path
    assert PROJECT_ROOT not in resolve_ledger_path().parents
