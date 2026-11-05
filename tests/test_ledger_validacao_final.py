"""Validação final do ledger: processos independentes, SQLite, reabertura, rollback e compatibilidade."""
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.operation_ledger import (
    CONFIRMADO,
    ENVIANDO,
    NAO_CRIADO,
    RESULTADO_DESCONHECIDO,
    LedgerError,
    OperationLedger,
)
from domain.models import TipoMovimento
from services.audit_service import AuditService
from tests.test_input_flows import make_row
from tests.test_operation_ledger import _ok, _orch

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "dados" / "operacoes_financeiras.sqlite3"


# --- Importação de histórico -----------------------------------------------------

def test_importacao_historica_bloqueia_novo_post(ledger_path):
    led = OperationLedger.inicializar(ledger_path)
    led.importar_historico("T_EXTRATO|EXT0000003908", CONFIRMADO, "verificado no SOMA 05/11", doc_id="5653763")
    led.close()
    orch = _orch(ledger_path, api_outcome=_ok())
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA, processo="T_EXTRATO", id_interno="EXT0000003908", doc_soma=""))
    assert not out.success
    orch.api.criar_saida.assert_not_called()


def test_importacao_nao_substitui_registo_existente_nem_exige_doc(ledger_path):
    led = OperationLedger.inicializar(ledger_path)
    led.importar_historico("K1", RESULTADO_DESCONHECIDO, "html 05/11")
    with pytest.raises(LedgerError):
        led.importar_historico("K1", CONFIRMADO, "tentativa", doc_id="1")
    with pytest.raises(LedgerError):
        led.importar_historico("K2", CONFIRMADO, "sem doc", doc_id="")
    with pytest.raises(LedgerError):
        led.importar_historico("K3", NAO_CRIADO, "não aceite")
    led.close()


# --- Processos independentes -----------------------------------------------------

_WORKER = textwrap.dedent("""
    import sys
    from core.operation_ledger import OperationLedger, LedgerError
    led = OperationLedger(sys.argv[1])
    try:
        led.registar_envio(sys.argv[2])
        print("ENVIO")
    except LedgerError:
        print("BLOQUEADO")
    finally:
        led.close()
""")


def test_concorrencia_entre_processos_independentes(tmp_path):
    ledger_file = tmp_path / "dados" / "ledger.sqlite3"
    OperationLedger.inicializar(ledger_file).close()
    worker = tmp_path / "worker.py"
    worker.write_text(_WORKER, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    procs = [
        subprocess.Popen([sys.executable, str(worker), str(ledger_file), "T_EXTRATO|EXT_PROC"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(ROOT))
        for _ in range(4)
    ]
    saidas = [p.communicate(timeout=120)[0].decode().strip() for p in procs]
    assert sorted(saidas) == ["BLOQUEADO", "BLOQUEADO", "BLOQUEADO", "ENVIO"], saidas


# --- Reinício, reabertura e falha de SQLite -----------------------------------------

def test_reinicio_com_envio_pendente_e_reconciliacao(ledger_path):
    led = OperationLedger.inicializar(ledger_path)
    led.registar_envio("K_INT")
    led.close()
    led2 = OperationLedger(ledger_path)
    try:
        assert led2.obter("K_INT").estado == ENVIANDO
        with pytest.raises(LedgerError):
            led2.registar_envio("K_INT")
        led2.reconciliar("K_INT", NAO_CRIADO, "pesquisa no SOMA sem resultados")
        led2.registar_envio("K_INT")
    finally:
        led2.close()


def test_falha_sqlite_durante_registo_de_envio_nao_permite_post(ledger_path):
    led = OperationLedger.inicializar(ledger_path)
    real = led._conn
    led._conn = MagicMock()
    led._conn.execute.side_effect = sqlite3.OperationalError("disk I/O error")
    with pytest.raises(LedgerError):
        led.registar_envio("K_IO")
    led._conn = real
    assert led.obter("K_IO") is None  # transação revertida, nada registado
    led.close()


def test_falha_sqlite_na_leitura_bloqueia_orquestrador(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.ledger._conn = MagicMock()
    orch.ledger._conn.execute.side_effect = sqlite3.OperationalError("database is locked")
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success
    orch.api.criar_saida.assert_not_called()


# --- Rollback com operações desconhecidas -------------------------------------------

def test_rollback_com_operacao_desconhecida_continua_bloqueado(tmp_path):
    ledger_file = tmp_path / "dados" / "ledger.sqlite3"
    app_nova = tmp_path / "app_nova"
    app_nova.mkdir()
    led = OperationLedger.inicializar(ledger_file)
    led.registar_envio("T_EXTRATO|EXT_RB")
    led.registar_resultado("T_EXTRATO|EXT_RB", RESULTADO_DESCONHECIDO, detalhe="timeout")
    led.close()
    shutil.rmtree(app_nova)  # rollback: código substituído
    orch = _orch(ledger_file, api_outcome=_ok())
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA, processo="T_EXTRATO", id_interno="EXT_RB"))
    assert not out.success
    orch.api.criar_saida.assert_not_called()


# --- Compatibilidade com a auditoria ----------------------------------------------

def test_auditoria_nao_corrige_linha_com_resultado_desconhecido():
    svc = AuditService.__new__(AuditService)
    svc.search_by_codigo = MagicMock()
    svc.search_by_descricao = MagicMock()
    row = make_row(tipo=TipoMovimento.SAIDA, doc_soma="RESULTADO_DESCONHECIDO")
    out = svc.audit_row(row)
    assert out.analyzed and out.inconsistent and not out.corrected
    svc.search_by_codigo.assert_not_called()
    svc.search_by_descricao.assert_not_called()


# --- Pausa financeira (rollback seguro) ----------------------------------------

def test_pausa_financeira_impede_qualquer_post_sem_alterar_ledger(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.ledger.pausa_path.write_text("rollback 05/11", encoding="utf-8")
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert not out.success and "pausados" in out.error_message
    orch.api.criar_saida.assert_not_called()
    orch.sheets.claim_row.assert_not_called()
    assert orch.ledger.obter("T_EXTRATO|EXT001") is None


def test_pausa_removida_permite_retomar(ledger_path):
    orch = _orch(ledger_path, api_outcome=_ok())
    orch.ledger.pausa_path.write_text("x", encoding="utf-8")
    orch.ledger.pausa_path.unlink()
    orch._confirm_and_settle = lambda row, doc: None
    out = orch.process_row(make_row(tipo=TipoMovimento.SAIDA))
    assert out.success
    orch.api.criar_saida.assert_called_once()
