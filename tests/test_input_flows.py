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

    assert outcome.success and outcome.doc_id == "Transferido"
    api.criar_transferencia.assert_not_called()
    kwargs = orch.sheets.mark_row_completed.call_args.kwargs
    assert kwargs["doc_id"] == "Transferido"
    assert "290734" in kwargs["dados_doc"]


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
    assert outcome.doc_id == "Transferido"
    assert "101" in outcome.dados_doc


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


# --- Transferido em DOC. SOMA -------------------------------------------------

def test_completed_transfer_writes_transferido_without_soma_link():
    from tests.test_origin_doc_update import FakeWorksheet, make_service

    origin = FakeWorksheet([["ID_INTERNO", "DOC. SOMA"], ["EXT001", ""]])
    service = make_service(origin)

    service.mark_row_completed(7, "Transferido", "Transferência 101", processo="T_EXTRATO", id_interno="EXT001")

    assert origin.updates == [("B2", [["Transferido"]])]
    written = {u["range"]: u["values"][0][0] for u in service._ws.updates[-1][1]}
    assert "Transferido" in written.values()
    assert not any("HYPERLINK" in str(v) for v in written.values())


@pytest.mark.parametrize("doc_id", ["TRF_149817", "transferido"])
def test_completed_row_rejects_old_trf_format(doc_id):
    from tests.test_origin_doc_update import FakeWorksheet, make_service

    service = make_service(FakeWorksheet([["ID_INTERNO", "DOC. SOMA"], ["EXT001", ""]]))
    with pytest.raises(ValueError):
        service.mark_row_completed(7, doc_id, processo="T_EXTRATO", id_interno="EXT001")


# --- Ciclo contínuo: um registo de cada vez até não haver candidatos ---------

def make_pending_orchestrator(rows, fresh=None, fail_rows=()):
    orch = DirectOrchestrator.__new__(DirectOrchestrator)
    orch.settings = SimpleNamespace(claim_stale_seconds=900, run_caixas_bancos=False, run_soma_sheet=False)
    orch.auth = MagicMock()
    orch.api = MagicMock()
    orch.sheets = MagicMock()
    sheet = {r.row_number: r for r in rows}
    orch.sheets.get_all_rows.side_effect = lambda **kw: list(sheet.values())
    fresh_rows = fresh or {}
    orch.sheets.get_row.side_effect = lambda idx: fresh_rows.get(idx, sheet.get(idx))
    processed = []

    def fake_process(row, dry_run=False):
        processed.append(row.row_number)
        ok = row.row_number not in fail_rows
        # Sucesso preenche o DOC. SOMA; falha deixa a linha EM ERRO (continua pendente).
        sheet[row.row_number] = make_row(
            row_number=row.row_number, id_interno=row.id_interno, tipo=row.tipo,
            doc_soma="Transferido" if ok else "", status="VALIDADO" if ok else "EM ERRO",
        )
        return OperationOutcome(ok, "x", row.tipo.value, row.row_number, 0)

    orch.process_row = fake_process
    return orch, processed


def test_loop_processes_one_row_at_a_time_until_no_candidates():
    rows = [make_row(row_number=n, id_interno=f"EXT{n}", tipo=TipoMovimento.TRANSFERENCIA) for n in (5, 6, 7)]
    orch, processed = make_pending_orchestrator(rows)

    orch.run_pending()

    assert processed == [5, 6, 7]
    # Uma procura de candidatos antes de cada registo + a procura final vazia.
    assert orch.sheets.get_all_rows.call_count == 4


def test_failed_row_is_not_retried_in_the_same_session():
    rows = [make_row(row_number=n, id_interno=f"EXT{n}") for n in (5, 6)]
    orch, processed = make_pending_orchestrator(rows, fail_rows={5})

    outcomes = orch.run_pending()

    assert processed == [5, 6]
    assert [o.success for o in outcomes] == [False, True]


def test_row_filled_meanwhile_is_skipped_and_next_empty_doc_is_used():
    rows = [make_row(row_number=n, id_interno=f"EXT{n}") for n in (5, 6)]
    fresh = {5: make_row(row_number=5, id_interno="EXT5", doc_soma="Transferido")}
    orch, processed = make_pending_orchestrator(rows, fresh=fresh)

    orch.run_pending()

    assert processed == [6]


def test_no_candidates_ends_session_without_processing():
    orch, processed = make_pending_orchestrator([make_row(doc_soma="5500123")])

    assert orch.run_pending() == []
    assert processed == []


def test_transferido_rows_are_not_pending():
    row = make_row(tipo=TipoMovimento.TRANSFERENCIA, doc_soma="Transferido")
    assert not DirectOrchestrator._is_pending(row, 900)


# --- Sessão única do orquestrador ----------------------------------------------

def test_second_session_is_refused_while_first_is_active(tmp_path):
    from core.run_lock import orchestrator_session

    lock = tmp_path / "orch.lock"
    with orchestrator_session(lock) as first:
        assert first is True
        with orchestrator_session(lock) as second:
            assert second is False
    with orchestrator_session(lock) as again:
        assert again is True


@pytest.mark.parametrize("method, args", [("run_pending", ()), ("run_target_rows", ([5],))])
def test_orchestrator_aborts_when_session_already_active(monkeypatch, method, args):
    from contextlib import contextmanager

    @contextmanager
    def busy():
        yield False

    monkeypatch.setattr("workflows.orchestrator.orchestrator_session", busy)
    orch, processed = make_pending_orchestrator([make_row(row_number=5, id_interno="EXT5")])

    assert getattr(orch, method)(*args) == []
    orch.auth.login.assert_not_called()
    assert processed == []
