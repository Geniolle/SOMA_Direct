from collections import Counter
from datetime import datetime

import pytest

from ronda_completa import (
    delete_soma_transfer,
    parse_transfer_table,
    parse_date,
    print_final_result,
    print_row_error,
    render_progress,
    resolve_interval,
    row_date_in_interval,
    transfer_key,
)


def test_resolve_interval_uses_prompt(monkeypatch):
    answers = iter(["01/08/2026", "31/08/2026"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    start, end = resolve_interval()

    assert start == datetime(2026, 8, 1)
    assert end == datetime(2026, 8, 31)


def test_resolve_interval_rejects_inverted_dates():
    with pytest.raises(ValueError, match="data inicial"):
        resolve_interval("31/08/2026", "01/08/2026")


def test_date_parser_and_interval_filter():
    start = parse_date("01/08/2026")
    end = parse_date("31/08/2026")

    assert row_date_in_interval("15/08/2026", start, end)
    assert not row_date_in_interval("01/09/2026", start, end)


def test_date_parser_accepts_date_without_separators():
    assert parse_date("09092026") == datetime(2026, 9, 9)


def test_parse_transfer_table_and_match_sheet_key():
    page = """
    <table><tr>
      <td><input id="check76586" value="290734"></td>
      <td>
        <a class="btn btn-danger bnt_excluir" id="290734"></a>
        <a class="btn btn-info btn_obs" data-dados="TRANSFERÊNCIA ENTRE CAIXAS N001"></a>
      </td>
      <td>Caixa Diário</td><td>€ 115,00</td>
      <td>Caixa Económica Montepio Geral - CC</td><td>€ 115,00</td>
      <td>03/09/2026</td>
    </tr></table>
    """

    transfers = parse_transfer_table(page)

    assert len(transfers) == 1
    assert transfers[0].transfer_id == "290734"
    assert transfers[0].observacao == "TRANSFERÊNCIA ENTRE CAIXAS N001"
    assert transfer_key(
        transfers[0].data,
        transfers[0].valor_saida,
        transfers[0].caixa_origem,
        transfers[0].caixa_destino,
    ) == transfer_key(
        "03/09/2026",
        "115,00",
        "CAIXA DIÁRIO",
        "CAIXA ECONÓMICA MONTEPIO GERAL [CONTA CORRENTE]",
    )


def test_delete_transfer_uses_official_endpoint_and_requires_success():
    class Response:
        @staticmethod
        def json():
            return {"status": 1}

    class Http:
        def __init__(self):
            self.call = None

        def post_ajax(self, url, data):
            self.call = (url, data)
            return Response()

    class Orchestrator:
        settings = type("Settings", (), {"site_base_url": "https://example.test/IVV/"})()
        http = Http()

    orchestrator = Orchestrator()
    delete_soma_transfer(orchestrator, "290734")

    assert orchestrator.http.call == (
        "https://example.test/IVV/sys/app/transferencias_caixas.php",
        {"id": "290734", "excluir": "1"},
    )


def test_progress_reuses_line_and_preserves_errors(capsys):
    render_progress(1, 4151)
    render_progress(2, 4151)
    print_row_error(104, "Falha de validação", 3, 4151)

    output = capsys.readouterr().out
    assert "\rLinhas da sheet no período: 1/4151" in output
    assert "\rLinhas da sheet no período: 2/4151" in output
    assert "Linha 104: Falha de validação\n" in output
    assert output.endswith("\rLinhas da sheet no período: 3/4151")


def test_final_result_is_clear_when_there_are_no_divergences(capsys):
    stats = Counter(confirmados=6, inversa_confirmada=6)

    print_final_result(
        datetime(2026, 9, 9),
        datetime(2026, 9, 9),
        True,
        stats,
        sheet_rows_count=6,
        soma_items_count=6,
        reverse_errors=[],
    )

    output = capsys.readouterr().out
    assert "Status: CONCLUÍDO SEM DIVERGÊNCIAS" in output
    assert "Linhas analisadas: 6" in output
    assert "Linhas corrigidas com segurança: 0" in output
    assert "Documentos vinculados corretamente: 6" in output
    assert "Nenhuma divergência encontrada nas duas validações." in output
