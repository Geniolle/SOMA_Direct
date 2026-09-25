from __future__ import annotations

from decimal import Decimal

import pytest

from services.repasse_service import (
    BalanceteSaidaItem,
    RepasseParsingError,
    RepasseReportService,
    SomaSessionExpired,
    format_decimal_pt,
    month_period,
    normalize_plano_conta,
    parse_decimal_pt,
    reconcile_repasse_items,
)
from services.sheets_service import GoogleSheetsService
from workflows import repasse_cli


REPASSE_HTML = """
<table class="export_tabela">
  <tr><th>Mês</th><th>Plano</th><th>Valor</th><th>Status</th></tr>
  <tr>
    <td>Janeiro</td>
    <td>Dízimos 10%<br>Oferta COVV 2%<br>Oferta Missões 1%<br>Oferta Novas Obras 2%</td>
    <td>340,89<br>68,18<br>34,09<br>68,18</td>
    <td>Conferido<br>Conferido<br>Conferido<br>Conferido</td>
  </tr>
  <tr><td>Total</td><td></td><td>511,34</td><td></td></tr>
</table>
"""


BALANCETE_HTML = """
<table class="export_tabela"><tr><td>ENTRADAS</td></tr></table>
<table class="export_tabela">
  <tr><th>Plano de Conta</th><th>VALOR SAÍDA TOTAL</th></tr>
  <tr><td>DÍZIMOS 10%</td><td>340,89</td></tr>
  <tr><td>OFERTA COVV 2%</td><td>68,18</td></tr>
  <tr><td>OFERTA MISSÕES 1%</td><td>34,09</td></tr>
  <tr><td>OFERTA NOVAS OBRAS 2%</td><td>68,18</td></tr>
  <tr><td>TOTAL SAÍDA</td><td>511,34</td></tr>
</table>
"""


def test_parse_reference_january_2023_and_reconcile_all_conferido():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)
    balancete = RepasseReportService.parse_balancete_html(BALANCETE_HTML, month_period(2023, 1))

    records = reconcile_repasse_items(
        repasses,
        {1: balancete},
        instituicao="BRAGA - PORTUGAL",
        data_execucao="25/09/2026 08:30:00",
    )

    assert len(records) == 4
    assert [record.resultado_validacao for record in records] == ["CONFERIDO"] * 4
    assert all(record.diferenca == Decimal("0.00") for record in records)


def test_conferido_mes_correto():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    balancete = RepasseReportService.parse_balancete_html(BALANCETE_HTML, month_period(2023, 1))

    record = reconcile_repasse_items(repasses, {1: balancete}, instituicao="BRAGA", data_execucao="x")[0]

    assert record.resultado_validacao == "CONFERIDO"
    assert record.diagnostico == "CONFERIDO"
    assert record.mes_match_alternativo == ""


def test_missing_plan_is_incomplete():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)
    balancete = RepasseReportService.parse_balancete_html(
        BALANCETE_HTML.replace("<tr><td>OFERTA MISSÕES 1%</td><td>34,09</td></tr>", ""),
        month_period(2023, 1),
    )
    records = reconcile_repasse_items(repasses, {1: balancete}, instituicao="BRAGA", data_execucao="x")
    misses = [record for record in records if record.plano_conta_repasse == "Oferta Missões 1%"]
    assert misses[0].resultado_validacao == "REPASSE INCOMPLETO"
    assert misses[0].diagnostico == "AUSENTE_NO_BALANCETE"
    assert misses[0].valor_balancete is None


def test_ausente_no_balancete():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    records = reconcile_repasse_items(repasses, {1: []}, instituicao="BRAGA", data_execucao="x")

    assert records[0].resultado_validacao == "REPASSE INCOMPLETO"
    assert records[0].diagnostico == "AUSENTE_NO_BALANCETE"
    assert records[0].valor_balancete is None


def test_different_value_is_incomplete_with_difference():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)
    balancete = RepasseReportService.parse_balancete_html(
        BALANCETE_HTML.replace("DÍZIMOS 10%</td><td>340,89", "DÍZIMOS 10%</td><td>341,89"),
        month_period(2023, 1),
    )
    records = reconcile_repasse_items(repasses, {1: balancete}, instituicao="BRAGA", data_execucao="x")
    dizimo = records[0]
    assert dizimo.resultado_validacao == "REPASSE INCOMPLETO"
    assert dizimo.diagnostico == "VALOR_DIVERGENTE"
    assert dizimo.diferenca == Decimal("1.00")


def test_valor_divergente():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    balancete = [
        BalanceteSaidaItem(month_period(2023, 1), "DÍZIMOS 10%", Decimal("341.89"))
    ]

    record = reconcile_repasse_items(repasses, {1: balancete}, instituicao="BRAGA", data_execucao="x")[0]

    assert record.resultado_validacao == "REPASSE INCOMPLETO"
    assert record.diagnostico == "VALOR_DIVERGENTE"
    assert record.valor_balancete == Decimal("341.89")


def test_multiple_candidates_are_not_chosen_as_conferido():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    balancete = RepasseReportService.parse_balancete_html(
        BALANCETE_HTML.replace(
            "<tr><td>DÍZIMOS 10%</td><td>340,89</td></tr>",
            "<tr><td>DÍZIMOS 10%</td><td>340,89</td></tr><tr><td>DÍZIMOS 10%</td><td>340,89</td></tr>",
        ),
        month_period(2023, 1),
    )
    records = reconcile_repasse_items(repasses, {1: balancete}, instituicao="BRAGA", data_execucao="x")
    assert records[0].resultado_validacao == "REPASSE INCOMPLETO"
    assert records[0].diagnostico == "MULTIPLOS_CANDIDATOS"
    assert "ambígua" in records[0].observacao


def test_mes_divergente_continua_incompleto():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    september = month_period(2023, 9)
    balancete = [
        BalanceteSaidaItem(september, "DÍZIMOS 10%", Decimal("340.89"))
    ]

    record = reconcile_repasse_items(
        repasses,
        {1: [], 9: balancete},
        instituicao="BRAGA",
        data_execucao="x",
    )[0]

    assert record.resultado_validacao == "REPASSE INCOMPLETO"
    assert record.diagnostico == "MES_DIVERGENTE"


def test_mes_divergente_guarda_mes_alternativo():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    september = month_period(2023, 9)
    balancete = [
        BalanceteSaidaItem(september, "DÍZIMOS 10%", Decimal("340.89"))
    ]

    record = reconcile_repasse_items(
        repasses,
        {1: [], 9: balancete},
        instituicao="BRAGA",
        data_execucao="x",
    )[0]

    assert record.mes_match_alternativo == "Setembro"
    assert record.observacao == "Match exato encontrado em outro mês: Setembro"


def test_multiplos_matches_alternativos_nao_escolhe_arbitrariamente():
    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    september = month_period(2023, 9)
    october = month_period(2023, 10)

    record = reconcile_repasse_items(
        repasses,
        {
            1: [],
            9: [BalanceteSaidaItem(september, "DÍZIMOS 10%", Decimal("340.89"))],
            10: [BalanceteSaidaItem(october, "DÍZIMOS 10%", Decimal("340.89"))],
        },
        instituicao="BRAGA",
        data_execucao="x",
    )[0]

    assert record.resultado_validacao == "REPASSE INCOMPLETO"
    assert record.diagnostico == "MULTIPLOS_CANDIDATOS"
    assert record.mes_match_alternativo == ""


def test_unexpected_html_and_expired_session_fail_explicitly():
    with pytest.raises(SomaSessionExpired):
        RepasseReportService.parse_repasse_html("<html>login senha</html>", 2023)
    with pytest.raises(SomaSessionExpired):
        RepasseReportService.parse_balancete_html("<html>sem tabela</html>", month_period(2023, 1))


def test_inconsistent_br_quantities_raise_parse_error():
    html = REPASSE_HTML.replace("Conferido<br>Conferido<br>Conferido<br>Conferido", "Conferido")
    with pytest.raises(RepasseParsingError):
        RepasseReportService.parse_repasse_html(html, 2023)


def test_money_normalization_month_boundaries_and_plan_normalization():
    assert parse_decimal_pt("1.234,56") == Decimal("1234.56")
    assert format_decimal_pt(Decimal("1234.50")) == "1234,50"
    assert normalize_plano_conta(" (-)  DÍZIMOS 10% ") == "dizimos 10%"
    assert month_period(2023, 2).data_fim == "28/02/2023"
    assert month_period(2024, 2).data_fim == "29/02/2024"


def test_sheet_upsert_header_and_no_duplication():
    class FakeWorksheet:
        def __init__(self):
            self.values = []
            self.batch_calls = []

        def get_all_values(self, *args, **kwargs):
            return [row[:] for row in self.values]

        def update(self, cell_range, values):
            self.values = [values[0][:]]

        def batch_update(self, updates, value_input_option=None):
            self.batch_calls.append(updates)
            for update in updates:
                row_number = int(update["range"].split(":")[0][1:])
                while len(self.values) < row_number:
                    self.values.append([""] * 17)
                self.values[row_number - 1] = update["values"][0][:]

    class FakeSpreadsheet:
        def __init__(self, ws):
            self.ws = ws

        def worksheet(self, name):
            return self.ws

    ws = FakeWorksheet()
    service = object.__new__(GoogleSheetsService)
    service.settings = type("Settings", (), {"sheet_repasse": "T_REPASSE"})()
    service._sh = FakeSpreadsheet(ws)
    service._col_letter = GoogleSheetsService._col_letter

    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    balancete = RepasseReportService.parse_balancete_html(BALANCETE_HTML, month_period(2023, 1))
    records = reconcile_repasse_items(repasses, {1: balancete}, instituicao="BRAGA", data_execucao="x")

    assert service.upsert_repasse_records(records) == 1
    assert service.upsert_repasse_records(records) == 1
    assert len(ws.values) == 2


def test_diagnostico_persistido_na_sheet():
    class FakeWorksheet:
        def __init__(self):
            self.values = []

        def get_all_values(self, *args, **kwargs):
            return [row[:] for row in self.values]

        def update(self, cell_range, values):
            self.values = [values[0][:]]

        def batch_update(self, updates, value_input_option=None):
            for update in updates:
                row_number = int(update["range"].split(":")[0][1:])
                while len(self.values) < row_number:
                    self.values.append([""] * 17)
                self.values[row_number - 1] = update["values"][0][:]

    class FakeSpreadsheet:
        def __init__(self, ws):
            self.ws = ws

        def worksheet(self, name):
            return self.ws

    ws = FakeWorksheet()
    service = object.__new__(GoogleSheetsService)
    service.settings = type("Settings", (), {"sheet_repasse": "T_REPASSE"})()
    service._sh = FakeSpreadsheet(ws)
    service._col_letter = GoogleSheetsService._col_letter

    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    september = month_period(2023, 9)
    records = reconcile_repasse_items(
        repasses,
        {1: [], 9: [BalanceteSaidaItem(september, "DÍZIMOS 10%", Decimal("340.89"))]},
        instituicao="BRAGA",
        data_execucao="x",
    )

    service.upsert_repasse_records(records)

    assert ws.values[0][14] == "DIAGNOSTICO"
    assert ws.values[0][15] == "MES_MATCH_ALTERNATIVO"
    assert ws.values[1][14] == "MES_DIVERGENTE"
    assert ws.values[1][15] == "Setembro"


def test_upsert_preserva_id_validacao():
    class FakeWorksheet:
        def __init__(self):
            self.values = []

        def get_all_values(self, *args, **kwargs):
            return [row[:] for row in self.values]

        def update(self, cell_range, values):
            self.values = [values[0][:]]

        def batch_update(self, updates, value_input_option=None):
            for update in updates:
                row_number = int(update["range"].split(":")[0][1:])
                while len(self.values) < row_number:
                    self.values.append([""] * 17)
                self.values[row_number - 1] = update["values"][0][:]

    class FakeSpreadsheet:
        def __init__(self, ws):
            self.ws = ws

        def worksheet(self, name):
            return self.ws

    ws = FakeWorksheet()
    service = object.__new__(GoogleSheetsService)
    service.settings = type("Settings", (), {"sheet_repasse": "T_REPASSE"})()
    service._sh = FakeSpreadsheet(ws)
    service._col_letter = GoogleSheetsService._col_letter

    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    balancete = RepasseReportService.parse_balancete_html(BALANCETE_HTML, month_period(2023, 1))
    first = reconcile_repasse_items(repasses, {1: balancete}, instituicao="BRAGA", data_execucao="x")
    service.upsert_repasse_records(first)
    existing = service.get_repasse_existing_ids()
    second = reconcile_repasse_items(
        repasses,
        {1: balancete},
        instituicao="BRAGA",
        data_execucao="y",
        existing_ids=existing,
    )
    service.upsert_repasse_records(second)

    assert ws.values[1][0] == first[0].id_validacao
    assert ws.values[1][0] == second[0].id_validacao


def test_upsert_nao_duplica():
    class FakeWorksheet:
        def __init__(self):
            self.values = []

        def get_all_values(self, *args, **kwargs):
            return [row[:] for row in self.values]

        def update(self, cell_range, values):
            self.values = [values[0][:]]

        def batch_update(self, updates, value_input_option=None):
            for update in updates:
                row_number = int(update["range"].split(":")[0][1:])
                while len(self.values) < row_number:
                    self.values.append([""] * 17)
                self.values[row_number - 1] = update["values"][0][:]

    class FakeSpreadsheet:
        def __init__(self, ws):
            self.ws = ws

        def worksheet(self, name):
            return self.ws

    ws = FakeWorksheet()
    service = object.__new__(GoogleSheetsService)
    service.settings = type("Settings", (), {"sheet_repasse": "T_REPASSE"})()
    service._sh = FakeSpreadsheet(ws)
    service._col_letter = GoogleSheetsService._col_letter

    repasses = RepasseReportService.parse_repasse_html(REPASSE_HTML, 2023)[:1]
    balancete = RepasseReportService.parse_balancete_html(BALANCETE_HTML, month_period(2023, 1))
    records = reconcile_repasse_items(repasses, {1: balancete}, instituicao="BRAGA", data_execucao="x")

    service.upsert_repasse_records(records)
    service.upsert_repasse_records(records)

    assert len(ws.values) == 2


def test_repasse_cli_defaults_to_no_write(monkeypatch):
    calls = []

    class FakeOrchestrator:
        def run(self, ano, dry_run=True, instituicao_id=None):
            calls.append((ano, dry_run, instituicao_id))
            return []

    monkeypatch.setattr(repasse_cli, "RepasseAuditOrchestrator", FakeOrchestrator)
    assert repasse_cli.main(["--ano", "2023"]) == 0
    assert calls == [(2023, True, None)]


def test_repasse_cli_apply_is_explicit(monkeypatch):
    calls = []

    class FakeOrchestrator:
        def run(self, ano, dry_run=True, instituicao_id=None):
            calls.append((ano, dry_run, instituicao_id))
            return []

    monkeypatch.setattr(repasse_cli, "RepasseAuditOrchestrator", FakeOrchestrator)
    assert repasse_cli.main(["--ano", "2023", "--apply"]) == 0
    assert calls == [(2023, False, None)]
