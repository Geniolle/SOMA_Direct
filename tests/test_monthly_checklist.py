from __future__ import annotations

from decimal import Decimal

from domain.models import ContaOrdemRow, TipoMovimento
from services.monthly_checklist_service import (
    FLOW_CONFERIDO,
    aggregate_contaordem,
    compare_balancete,
    filter_month_rows,
    find_first_period,
    format_money,
    is_transferencia,
    match_fluxo_rows,
    month_period,
    normalize_plan,
    parse_balancete_html,
    parse_decimal_money,
    parse_fluxo_caixa_html,
    representative_plans,
    validate_internal_rows,
)
from workflows.monthly_checklist_orchestrator import (
    MonthlyChecklistOrchestrator,
    MonthlyChecklistResult,
)


BALANCETE_HTML = """
<table class="export_tabela">
<tr><th>Plano</th><th>VALOR ENTRADA TOTAL</th></tr>
<tr><td>DOAÇÕES - DÍZIMOS E OFERTAS</td><td>100,00</td></tr>
<tr><td>TOTAL ENTRADA</td><td>100,00</td></tr>
</table>
<table class="export_tabela">
<tr><th>Plano</th><th>VALOR SAÍDA TOTAL</th></tr>
<tr><td>DESPESAS BANCÁRIAS</td><td>10,00</td></tr>
<tr><td>TOTAL SAÍDA</td><td>10,00</td></tr>
</table>
"""


FLUXO_HTML = """
<table class="export_tabela">
<tr><td>Movimentação do(s) Caixa(s)</td></tr>
<tr><th>DESCRIÇÃO</th><th>PLANO DE CONTAS</th><th>PLANO CONTÁBIL</th><th>CENTRO CUSTO</th><th>CAIXA:</th><th>FORNECEDOR</th><th>NF</th><th>DATA VENCIMENTO / ENTRADA:</th><th>DATA BAIXA:</th><th>VALOR</th></tr>
<tr><td>Dízimo</td><td>DOAÇÕES - DÍZIMOS E OFERTAS</td><td>3.1</td><td>CC</td><td>CAIXA DIÁRIO</td><td></td><td></td><td>01/01/2024</td><td>01/01/2024</td><td>100,00</td></tr>
<tr><td>Banco</td><td>DESPESAS BANCÁRIAS</td><td>4.4</td><td>CC</td><td>BANCO</td><td></td><td></td><td>02/01/2024</td><td>02/01/2024</td><td>-10,00</td></tr>
</table>
"""


def row(
    row_number,
    data,
    tipo,
    valor,
    plano,
    id_interno="ID1",
    desc="Dízimo",
):
    raw = {
        "DATA MOV.": data,
        "TIPO": tipo,
        "IMPORTÂNCIA": valor,
        "PLANO DE CONTA": plano,
        "ID_INTERNO": id_interno,
        "AUDITORIA": "",
        "DOC. SOMA": "123",
        "DESCRIÇÃO": desc,
        "DESCRIÇÃO SOMA": desc,
        "CENTRO DE CUSTO": "CC",
        "CAIXA": "CAIXA DIÁRIO",
        "FORMA DE PAGAMENTO": "Transferência",
        "PROCESSO": "T_EXTRATO",
    }
    return ContaOrdemRow.from_dict(row_number, raw)


def test_descoberta_data_minima_contaordem():
    period, count = find_first_period([
        row(2, "15/02/2024", "Entrada", "1,00", "A"),
        row(3, "01/01/2024", "Entrada", "1,00", "A"),
        row(4, "20/01/2024", "Entrada", "1,00", "A"),
    ])
    assert period.label == "01/2024"
    assert count == 2


def test_calculo_inicio_fim_mes_e_fevereiro_bissexto():
    assert month_period(2024, 2).data_fim == "29/02/2024"
    assert month_period(2023, 2).data_fim == "28/02/2023"


def test_filtro_mensal_e_exclusao_transferencia():
    rows = [
        row(2, "01/01/2024", "Entrada", "10,00", "A"),
        row(3, "02/01/2024", "Transferência", "10,00", "A"),
        row(4, "01/02/2024", "Entrada", "10,00", "A"),
    ]
    selected, transfers = filter_month_rows(rows, month_period(2024, 1))
    assert len(selected) == 1
    assert transfers == 1


def test_transferencia_normalizacao():
    assert is_transferencia(" transferência ")
    assert is_transferencia("TRANSFERÊNCIA")


def test_decimal_e_normalizacao_plano():
    assert parse_decimal_money("1.234,56") == Decimal("1234.56")
    assert normalize_plan(" DÍZIMOS   E OFERTAS ") == "dizimos e ofertas"


def test_agrupamento_entrada_saida():
    selected, _ = filter_month_rows([
        row(2, "01/01/2024", "Entrada", "60,00", "DOAÇÕES"),
        row(3, "02/01/2024", "Entrada", "40,00", "doações"),
        row(4, "02/01/2024", "Saída", "10,00", "DESPESAS"),
    ], month_period(2024, 1))
    totals = aggregate_contaordem(selected)
    assert totals[("Entrada", "doacoes")] == Decimal("100.00")
    assert totals[("Saída", "despesas")] == Decimal("10.00")


def test_parsing_balancete_e_comparacao_conferido_divergente_ausente():
    items = parse_balancete_html(BALANCETE_HTML)
    assert len(items) == 2
    selected, _ = filter_month_rows([
        row(2, "01/01/2024", "Entrada", "100,00", "DOAÇÕES - DÍZIMOS E OFERTAS"),
        row(3, "02/01/2024", "Saída", "11,00", "DESPESAS BANCÁRIAS"),
        row(4, "02/01/2024", "Entrada", "5,00", "PLANO AUSENTE"),
    ], month_period(2024, 1))
    comparisons = compare_balancete(aggregate_contaordem(selected), representative_plans(selected), items)
    by_plan = {normalize_plan(item.plano_conta): item for item in comparisons}
    assert by_plan["doacoes - dizimos e ofertas"].resultado == "CONFERIDO"
    assert by_plan["despesas bancarias"].resultado == "DIVERGENTE"
    assert by_plan["plano ausente"].resultado == "AUSENTE_NO_BALANCETE"


def test_parsing_fluxo_e_match_individual():
    fluxo = parse_fluxo_caixa_html(FLUXO_HTML)
    selected, _ = filter_month_rows([
        row(2, "01/01/2024", "Entrada", "100,00", "DOAÇÕES - DÍZIMOS E OFERTAS"),
    ], month_period(2024, 1))
    result = match_fluxo_rows(selected, fluxo)[0]
    assert result.resultado == "CONFERIDO"
    assert result.auditoria_proposta == FLOW_CONFERIDO


def test_fluxo_registo_ausente_valor_plano_multiplos():
    fluxo = parse_fluxo_caixa_html(FLUXO_HTML)
    selected, _ = filter_month_rows([
        row(2, "05/01/2024", "Entrada", "100,00", "DOAÇÕES - DÍZIMOS E OFERTAS", "A"),
        row(3, "01/01/2024", "Entrada", "99,00", "DOAÇÕES - DÍZIMOS E OFERTAS", "B"),
        row(4, "01/01/2024", "Entrada", "100,00", "OUTRO PLANO", "C"),
    ], month_period(2024, 1))
    results = match_fluxo_rows(selected, fluxo)
    assert results[0].auditoria_proposta == "Erro: data divergente no Fluxo de Caixa"
    assert results[1].auditoria_proposta == "Erro: valor divergente no Fluxo de Caixa"
    assert results[2].auditoria_proposta == "Erro: Plano de Conta divergente no Fluxo de Caixa"


def test_fluxo_multiplos_candidatos():
    html = FLUXO_HTML.replace("</table>", "<tr><td>Dízimo 2</td><td>DOAÇÕES - DÍZIMOS E OFERTAS</td><td>3.1</td><td>CC</td><td>CAIXA DIÁRIO</td><td></td><td></td><td>01/01/2024</td><td>01/01/2024</td><td>100,00</td></tr></table>")
    fluxo = parse_fluxo_caixa_html(html)
    selected, _ = filter_month_rows([
        row(2, "01/01/2024", "Entrada", "100,00", "DOAÇÕES - DÍZIMOS E OFERTAS", "A", desc="Sem match descrição"),
    ], month_period(2024, 1))
    result = match_fluxo_rows(selected, fluxo)[0]
    assert result.resultado == "MULTIPLOS_CANDIDATOS"


def test_dry_run_nao_escreve_e_apply_escreve_apenas_auditoria_por_id():
    class FakeWorksheet:
        def __init__(self):
            self.headers = ["ID_INTERNO", "AUDITORIA", "OUTRA"]
            self.rows = {2: ["ID1", "", "preservar"]}
            self.updates = []

        def row_values(self, row_number):
            if row_number == 1:
                return self.headers
            return self.rows[row_number]

        def get_all_values(self):
            return [self.headers, self.rows[2]]

        def batch_update(self, updates):
            self.updates.extend(updates)
            for update in updates:
                assert update["range"] == "B2"
                self.rows[2][1] = update["values"][0][0]

    class FakeSheets:
        def __init__(self):
            self._ws = FakeWorksheet()

        def get_headers(self):
            return self._ws.headers

        @staticmethod
        def _col_letter(col_idx):
            return "AB"[col_idx - 1]

    selected, _ = filter_month_rows([
        row(2, "01/01/2024", "Entrada", "100,00", "DOAÇÕES - DÍZIMOS E OFERTAS", "ID1"),
    ], month_period(2024, 1))
    result = match_fluxo_rows(selected, parse_fluxo_caixa_html(FLUXO_HTML))[0]
    orch = object.__new__(MonthlyChecklistOrchestrator)
    orch.sheets = FakeSheets()
    assert orch.sheets._ws.rows[2] == ["ID1", "", "preservar"]
    orch._apply_auditoria([result])
    assert orch.sheets._ws.rows[2] == ["ID1", "Conferido", "preservar"]


def test_transferencia_nao_altera_auditoria():
    selected, transfers = filter_month_rows([
        row(2, "01/01/2024", "Transferência", "100,00", "A", "IDT"),
    ], month_period(2024, 1))
    assert selected == []
    assert transfers == 1


def test_auditoria_interna_confere_origem_e_soma():
    selected, _ = filter_month_rows([
        row(2, "01/01/2024", "Entrada", "100,00", "DOAÇÕES - DÍZIMOS E OFERTAS", "ID1"),
    ], month_period(2024, 1))
    source = {
        "T_EXTRATO": [{
            "ID_INTERNO": "ID1",
            "DATA MOV.": "01/01/2024",
            "IMPORTÂNCIA": "100,00",
            "DOC. SOMA": "123",
        }]
    }
    soma = [{
        "CODIGO": "123",
        "DESCRIÇÃO": "Dízimo",
        "PAGAMENTO": "01/01/2024",
        "VALOR": "100,00",
    }]

    result = validate_internal_rows(selected, source, soma)[0]

    assert result.resultado == "CONFERIDO"
    assert result.auditoria_proposta == "Conferido"


def test_auditoria_interna_descreve_inconsistencias():
    selected, _ = filter_month_rows([
        row(2, "01/01/2024", "Entrada", "100,00", "DOAÇÕES - DÍZIMOS E OFERTAS", "ID1"),
    ], month_period(2024, 1))
    source = {
        "T_EXTRATO": [{
            "ID_INTERNO": "ID1",
            "DATA MOV.": "02/01/2024",
            "IMPORTÂNCIA": "99,00",
            "DOC. SOMA": "999",
        }]
    }
    soma = []

    result = validate_internal_rows(selected, source, soma)[0]

    assert result.resultado == "DIVERGENTE"
    assert "Data divergente na origem" in result.auditoria_proposta
    assert "Valor divergente na origem" in result.auditoria_proposta
    assert "DOC.SOMA divergente na origem" in result.auditoria_proposta
    assert "DOC.SOMA não encontrado na sheet SOMA" in result.auditoria_proposta


def test_monthly_checklist_cli_supports_direct_file_execution(tmp_path):
    import subprocess
    import sys
    from config.paths import PROJECT_ROOT

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "workflows" / "monthly_checklist_cli.py"),
            "--help",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--primeiro-periodo" in result.stdout


def test_descoberta_primeiro_periodo_ignora_linhas_ja_auditadas():
    rows = [
        row(2, "01/01/2024", "Entrada", "10,00", "A"),
        row(3, "15/01/2024", "Entrada", "10,00", "A"),
        row(4, "05/02/2024", "Entrada", "20,00", "A"),
        row(5, "10/02/2024", "Entrada", "20,00", "A"),
    ]
    rows[0].auditoria = "Conferido"
    rows[1].auditoria = "DIVERGENTE"

    period, count = find_first_period(rows)
    assert period is not None
    assert period.label == "02/2024"
    assert count == 2


def test_descoberta_retorna_none_quando_todas_linhas_auditadas():
    rows = [
        row(2, "01/01/2024", "Entrada", "10,00", "A"),
    ]
    rows[0].auditoria = "Conferido"
    period, count = find_first_period(rows)
    assert period is None
    assert count == 0


def test_find_first_period_com_exclude_periods():
    rows = [
        row(2, "01/01/2024", "Entrada", "10,00", "A"),
        row(3, "05/02/2024", "Entrada", "20,00", "A"),
    ]
    period, count = find_first_period(rows, exclude_periods={"01/2024"})
    assert period is not None
    assert period.label == "02/2024"
    assert count == 1


def test_cli_modo_continuo_chama_run_until_complete(monkeypatch):
    from workflows import monthly_checklist_cli

    class FakeOrchestrator:
        def __init__(self):
            self.run_until_complete_called_with = None

        def run_until_complete(self, apply=True, only_empty_auditoria=True, max_periods=None, only_conferido=False):
            self.run_until_complete_called_with = (apply, only_empty_auditoria, max_periods, only_conferido)
            return [
                MonthlyChecklistResult(
                    period=month_period(2024, 1),
                    total_month_rows=5,
                    transfer_excluded=0,
                    eligible_rows=[],
                    balancete_comparisons=[],
                    fluxo_results=[],
                    internal_results=[],
                    rows_to_validate=[],
                )
            ]

    fake_orch = FakeOrchestrator()
    monkeypatch.setattr(monthly_checklist_cli, "MonthlyChecklistOrchestrator", lambda: fake_orch)

    exit_code = monthly_checklist_cli.main(["--apenas-conferidos", "--max-periodos", "3"])
    assert exit_code == 0
    assert fake_orch.run_until_complete_called_with == (True, True, 3, True)

    exit_code = monthly_checklist_cli.main(["--dry-run"])
    assert exit_code == 0
    assert fake_orch.run_until_complete_called_with == (False, True, None, False)


def test_cli_modo_periodo_especifico(monkeypatch):
    from workflows import monthly_checklist_cli

    class FakeOrchestrator:
        def __init__(self):
            self.run_called_with = None

        def run(self, ano, mes, apply=False, only_empty_auditoria=True, only_conferido=False):
            self.run_called_with = (ano, mes, apply, only_empty_auditoria, only_conferido)
            return MonthlyChecklistResult(
                period=month_period(ano, mes),
                total_month_rows=5,
                transfer_excluded=0,
                eligible_rows=[],
                balancete_comparisons=[],
                fluxo_results=[],
                internal_results=[],
                rows_to_validate=[],
            )

    fake_orch = FakeOrchestrator()
    monkeypatch.setattr(monthly_checklist_cli, "MonthlyChecklistOrchestrator", lambda: fake_orch)
    monkeypatch.setattr(monthly_checklist_cli, "print_monthly_checklist_report", lambda res: None)

    exit_code = monthly_checklist_cli.main(["--ano", "2024", "--mes", "3", "--apply"])
    assert exit_code == 0
    assert fake_orch.run_called_with == (2024, 3, True, True, False)


def test_run_until_complete_orchestrator(monkeypatch):
    orch = object.__new__(MonthlyChecklistOrchestrator)
    fake_auth = type("FakeAuth", (), {"login": lambda self: True})()
    orch.auth = fake_auth

    discovered = [
        (month_period(2024, 1), 10),
        (month_period(2024, 2), 15),
        (None, 0),
    ]

    def fake_discover(only_empty_auditoria=True, exclude_periods=None):
        if discovered:
            return discovered.pop(0)
        return None, 0

    runs = []

    def fake_run(ano, mes, apply=False, only_empty_auditoria=True, only_conferido=False):
        runs.append((ano, mes, apply, only_conferido))
        return MonthlyChecklistResult(
            period=month_period(ano, mes),
            total_month_rows=10,
            transfer_excluded=0,
            eligible_rows=[],
            balancete_comparisons=[],
            fluxo_results=[],
            internal_results=[],
            rows_to_validate=[],
        )

    orch.discover_first_period = fake_discover
    orch.run = fake_run

    results = orch.run_until_complete(apply=True, only_conferido=True, sleep_between_seconds=0)
    assert len(results) == 2
    assert runs == [
        (2024, 1, True, True),
        (2024, 2, True, True),
    ]


def test_load_source_and_soma_records_uses_cache():
    from workflows.monthly_checklist_orchestrator import _with_sheets_retry

    orch = object.__new__(MonthlyChecklistOrchestrator)
    orch._source_records_cache = {}
    orch._soma_records_cache = None
    orch.settings = type("FakeSettings", (), {"sheet_soma": "SOMA"})()

    class FakeWorksheet:
        def __init__(self, name):
            self.name = name
            self.read_count = 0

        def get_all_values(self):
            self.read_count += 1
            return [["ID_INTERNO", "DATA MOV."], ["1", "01/01/2024"]]

    class FakeSpreadsheet:
        def __init__(self):
            self.sheets = {
                "SOMA": FakeWorksheet("SOMA"),
                "T_EXTRATO": FakeWorksheet("T_EXTRATO"),
            }
            self.worksheet_calls = 0

        def worksheet(self, name):
            self.worksheet_calls += 1
            return self.sheets[name]

    sh = FakeSpreadsheet()
    orch.sheets = type("FakeSheets", (), {"_sh": sh, "_gc": None})()

    # Load SOMA first time
    soma1 = orch._load_soma_records()
    assert len(soma1) == 1
    assert sh.sheets["SOMA"].read_count == 1

    # Load SOMA second time -> should come from cache
    soma2 = orch._load_soma_records()
    assert soma2 is soma1
    assert sh.sheets["SOMA"].read_count == 1

    # Load source records
    test_rows = [row(2, "01/01/2024", "Entrada", "10,00", "A")]
    source1 = orch._load_source_records(test_rows)
    assert "T_EXTRATO" in source1
    assert sh.sheets["T_EXTRATO"].read_count == 1

    # Load source records second time -> should come from cache
    source2 = orch._load_source_records(test_rows)
    assert source2["T_EXTRATO"] is source1["T_EXTRATO"]
    assert sh.sheets["T_EXTRATO"].read_count == 1


def test_with_sheets_retry_backs_off_on_429():
    from workflows.monthly_checklist_orchestrator import _with_sheets_retry

    attempts = 0

    def flaky_call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("APIError: [429] Quota exceeded for quota metric 'Read requests'")
        return "SUCCESS"

    result = _with_sheets_retry(flaky_call, max_retries=5, initial_wait=0.001)
    assert result == "SUCCESS"
    assert attempts == 3



