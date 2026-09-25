from __future__ import annotations

import inspect
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import ContaOrdemRow, TipoMovimento, norm_basic
from services.monthly_checklist_service import (
    BalanceteComparison,
    ChecklistPeriod,
    FluxoMatchResult,
    InternalAuditResult,
    MonthlyChecklistSomaReports,
    ContaOrdemOriginValidationResult,
    aggregate_contaordem,
    compare_balancete,
    filter_month_rows,
    find_first_period,
    format_money,
    is_empty_auditoria,
    match_fluxo_rows,
    month_period,
    parse_balancete_html,
    parse_fluxo_caixa_html,
    representative_plans,
    row_to_checklist,
    summarize_fluxo,
    validate_contaordem_origin_rows,
    validate_internal_rows,
)
from services.sheets_service import (
    EXTERNAL_SOURCE_SHEETS,
    EXTERNAL_SOURCE_SPREADSHEET_URL,
    SOURCE_SHEETS,
    GoogleSheetsService,
)


@dataclass(frozen=True)
class MonthlyChecklistResult:
    period: ChecklistPeriod
    total_month_rows: int
    transfer_excluded: int
    eligible_rows: List
    balancete_comparisons: List[BalanceteComparison]
    fluxo_results: List[FluxoMatchResult]
    internal_results: List[InternalAuditResult]
    rows_to_validate: List = field(default_factory=list)


@dataclass(frozen=True)
class ContaOrdemOriginValidationRun:
    period: Optional[ChecklistPeriod]
    total_rows: int
    results: List[ContaOrdemOriginValidationResult]
    applied: int = 0


class MonthlyChecklistOrchestrator:
    def __init__(self, settings: Optional[Settings] = None, sheets: Optional[GoogleSheetsService] = None):
        self.settings = settings or Settings.from_env()
        self.http = ResilientSession(timeout=self.settings.timeout_seconds, verify_tls=self.settings.verify_tls)
        self.auth = SomaAuthenticator(self.settings, self.http)
        self.sheets = sheets or GoogleSheetsService(self.settings)
        self.reports = MonthlyChecklistSomaReports(self.settings, self.http)
        self._source_records_cache: dict[str, list[dict]] = {}
        self._soma_records_cache: Optional[list[dict]] = None

    def discover_first_period(
        self,
        only_empty_auditoria: bool = True,
        exclude_periods: Optional[set[str]] = None,
        all_rows: Optional[List[ContaOrdemRow]] = None,
    ) -> tuple[Optional[ChecklistPeriod], int]:
        rows = all_rows if all_rows is not None else self.sheets.get_all_rows(only_entrada_saida=False)
        return find_first_period(
            rows,
            only_empty_auditoria=only_empty_auditoria,
            exclude_periods=exclude_periods,
        )

    def run(
        self,
        ano: int,
        mes: int,
        apply: bool = False,
        only_empty_auditoria: bool = True,
        only_conferido: bool = False,
        all_rows: Optional[List[ContaOrdemRow]] = None,
    ) -> MonthlyChecklistResult:
        if not self.auth.login():
            raise RuntimeError("Não foi possível autenticar no SOMA.")
        period = month_period(ano, mes)
        if all_rows is None:
            all_rows = self.sheets.get_all_rows(only_entrada_saida=False)
        total_month_rows = sum(
            1 for row in all_rows
            if _row_in_period(row, period)
        )
        eligible_rows, transfer_excluded = filter_month_rows(all_rows, period)
        conta_totals = aggregate_contaordem(eligible_rows)
        conta_plans = representative_plans(eligible_rows)

        balancete_items = parse_balancete_html(self.reports.fetch_balancete_html(period))
        balancete_comparisons = compare_balancete(conta_totals, conta_plans, balancete_items)

        if only_empty_auditoria:
            rows_to_validate = [row for row in eligible_rows if is_empty_auditoria(row.auditoria)]
        else:
            rows_to_validate = eligible_rows

        fluxo_items = parse_fluxo_caixa_html(self.reports.fetch_fluxo_caixa_html(period))
        fluxo_results = match_fluxo_rows(rows_to_validate, fluxo_items)
        internal_results = validate_internal_rows(
            rows_to_validate,
            self._load_source_records(rows_to_validate),
            self._load_soma_records(),
        )

        if apply:
            applied = self._apply_auditoria(internal_results, only_conferido=only_conferido, all_rows=all_rows)
            print(f"-> [CONTAORDEM] {applied} registo(s) gravado(s) na coluna AUDITORIA para {period.label}.")

        return MonthlyChecklistResult(
            period=period,
            total_month_rows=total_month_rows,
            transfer_excluded=transfer_excluded,
            eligible_rows=eligible_rows,
            balancete_comparisons=balancete_comparisons,
            fluxo_results=fluxo_results,
            internal_results=internal_results,
            rows_to_validate=rows_to_validate,
        )

    def run_until_complete(
        self,
        apply: bool = False,
        only_empty_auditoria: bool = True,
        max_periods: Optional[int] = None,
        only_conferido: bool = False,
        sleep_between_seconds: float = 2.0,
    ) -> List[MonthlyChecklistResult]:
        if not self.auth.login():
            raise RuntimeError("Não foi possível autenticar no SOMA.")

        processed_periods: set[str] = set()
        results: List[MonthlyChecklistResult] = []

        all_rows: Optional[List[ContaOrdemRow]] = None
        if hasattr(self, "sheets") and self.sheets is not None:
            all_rows = self.sheets.get_all_rows(only_entrada_saida=False)

        while True:
            if max_periods is not None and len(results) >= max_periods:
                print(f"\nLimite máximo de períodos atingido ({max_periods}).")
                break

            if all_rows is not None:
                period, count = find_first_period(
                    all_rows,
                    only_empty_auditoria=only_empty_auditoria,
                    exclude_periods=processed_periods,
                )
            else:
                period, count = self.discover_first_period(
                    only_empty_auditoria=only_empty_auditoria,
                    exclude_periods=processed_periods,
                )

            if period is None or count == 0:
                print("\nNenhum outro período com registos pendentes encontrado.")
                break

            print()
            print("=" * 60)
            print(f"Iniciando análise do período {period.label} ({count} registos pendentes)")
            print("=" * 60)

            sig = inspect.signature(self.run)
            run_kwargs = {
                "ano": period.ano,
                "mes": period.mes,
                "apply": apply,
                "only_empty_auditoria": only_empty_auditoria,
                "only_conferido": only_conferido,
            }
            if "all_rows" in sig.parameters:
                run_kwargs["all_rows"] = all_rows

            result = self.run(**run_kwargs)
            print_monthly_checklist_report(result)
            processed_periods.add(period.label)
            results.append(result)

            if apply:
                print(f"-> Folha CONTAORDEM atualizada com sucesso para {period.label}.")

            if sleep_between_seconds > 0:
                time.sleep(sleep_between_seconds)

        print_overall_summary(results, apply=apply)
        return results

    def validate_origin_column(
        self,
        ano: Optional[int] = None,
        mes: Optional[int] = None,
        apply: bool = False,
    ) -> ContaOrdemOriginValidationRun:
        all_rows = self.sheets.get_all_rows(only_entrada_saida=False)
        period = month_period(ano, mes) if ano is not None and mes is not None else None
        if period is not None:
            rows, _ = filter_month_rows(all_rows, period)
        else:
            rows = [
                row_to_checklist(row)
                for row in all_rows
                if row.tipo in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA)
            ]

        results = validate_contaordem_origin_rows(rows, self._load_source_records(rows), self._load_soma_records())
        applied = 0
        if apply:
            applied = self._apply_origem(results, all_rows=all_rows)
            print(f"-> [CONTAORDEM] {applied} registo(s) gravado(s) na coluna ORIGEM.")
        return ContaOrdemOriginValidationRun(
            period=period,
            total_rows=len(rows),
            results=results,
            applied=applied,
        )

    def _load_source_records(self, rows: List) -> dict[str, list[dict]]:
        processes = {row.processo for row in rows if row.processo}
        out: dict[str, list[dict]] = {}
        for process in processes:
            source_name = SOURCE_SHEETS.get(norm_basic(process))
            if not source_name:
                out[process] = []
                continue
            if source_name not in self._source_records_cache:
                spreadsheet = self.sheets._sh
                if source_name in EXTERNAL_SOURCE_SHEETS:
                    spreadsheet = _with_sheets_retry(
                        lambda: self.sheets._gc.open_by_url(EXTERNAL_SOURCE_SPREADSHEET_URL)
                    )
                ws = _with_sheets_retry(lambda s=spreadsheet, n=source_name: s.worksheet(n))
                self._source_records_cache[source_name] = _worksheet_records(ws)
            out[process] = self._source_records_cache[source_name]
        return out

    def _load_soma_records(self) -> list[dict]:
        if self._soma_records_cache is None:
            ws = _with_sheets_retry(lambda: self.sheets._sh.worksheet(self.settings.sheet_soma))
            self._soma_records_cache = _worksheet_records(ws)
        return self._soma_records_cache

    def _apply_auditoria(
        self,
        results: List[InternalAuditResult],
        only_conferido: bool = False,
        all_rows: Optional[List[ContaOrdemRow]] = None,
    ) -> int:
        headers = self.sheets.get_headers()
        header_map = {norm_basic(header): idx for idx, header in enumerate(headers)}
        auditoria_idx = header_map.get(norm_basic("AUDITORIA"))
        id_idx = header_map.get(norm_basic("ID_INTERNO"))
        if auditoria_idx is None or id_idx is None:
            raise RuntimeError("CONTAORDEM precisa das colunas AUDITORIA e ID_INTERNO")

        id_by_row: dict[int, str] = {}
        if all_rows is not None:
            id_by_row = {r.row_number: r.id_interno for r in all_rows}
        else:
            all_values = _with_sheets_retry(lambda: self.sheets._ws.get_all_values())
            for idx, r in enumerate(all_values, start=1):
                if id_idx < len(r):
                    id_by_row[idx] = r[id_idx].strip()

        updates = []
        applied_count = 0
        for result in results:
            if only_conferido and result.resultado != "CONFERIDO":
                continue
            row = result.row
            if not row.id_interno:
                continue
            current_id = id_by_row.get(row.row_number, "")
            if current_id != row.id_interno:
                continue
            cell = f"{self.sheets._col_letter(auditoria_idx + 1)}{row.row_number}"
            updates.append({"range": cell, "values": [[result.auditoria_proposta]]})
            applied_count += 1
        if updates:
            _with_sheets_retry(lambda: self.sheets._ws.batch_update(updates))
        return applied_count

    def _apply_origem(
        self,
        results: List[ContaOrdemOriginValidationResult],
        all_rows: Optional[List[ContaOrdemRow]] = None,
    ) -> int:
        headers = self.sheets.get_headers()
        header_map = {norm_basic(header): idx for idx, header in enumerate(headers)}
        origem_idx = header_map.get(norm_basic("ORIGEM"))
        id_idx = header_map.get(norm_basic("ID_INTERNO"))
        if origem_idx is None or id_idx is None:
            raise RuntimeError("CONTAORDEM precisa das colunas ORIGEM e ID_INTERNO")

        id_by_row: dict[int, str] = {}
        if all_rows is not None:
            id_by_row = {r.row_number: r.id_interno for r in all_rows}
        else:
            all_values = _with_sheets_retry(lambda: self.sheets._ws.get_all_values())
            for idx, r in enumerate(all_values, start=1):
                if id_idx < len(r):
                    id_by_row[idx] = r[id_idx].strip()

        updates = []
        applied_count = 0
        for result in results:
            row = result.row
            current_id = id_by_row.get(row.row_number, "")
            if current_id != row.id_interno:
                continue
            cell = f"{self.sheets._col_letter(origem_idx + 1)}{row.row_number}"
            updates.append({"range": cell, "values": [[result.origem_proposta]]})
            applied_count += 1
        if updates:
            _with_sheets_retry(lambda: self.sheets._ws.batch_update(updates))
        return applied_count


def _with_sheets_retry(func: Callable[[], Any], max_retries: int = 5, initial_wait: float = 20.0) -> Any:
    wait = initial_wait
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            err_str = str(e)
            if ("429" in err_str or "Quota exceeded" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < max_retries - 1:
                print(f"[AVISO] Cota da Google Sheets atingida (429). Aguardando {int(wait)}s antes da tentativa {attempt + 2}/{max_retries}...")
                time.sleep(wait)
                wait *= 1.5
            else:
                raise


def _row_in_period(row: ContaOrdemRow, period: ChecklistPeriod) -> bool:
    from services.monthly_checklist_service import parse_date_key

    row_date = parse_date_key(row.data_mov)
    start = parse_date_key(period.data_inicio)
    end = parse_date_key(period.data_fim)
    return bool(row_date and start and end and start <= row_date <= end)


def _worksheet_records(worksheet) -> list[dict]:
    values = _with_sheets_retry(lambda: worksheet.get_all_values())
    if not values:
        return []
    headers = [str(value).strip() for value in values[0]]
    records = []
    for row in values[1:]:
        record = {}
        for index, header in enumerate(headers):
            if not header or header in record:
                continue
            record[header] = row[index] if index < len(row) else ""
        records.append(record)
    return records



def print_monthly_checklist_report(result: MonthlyChecklistResult) -> None:
    period = result.period
    print(f"CHECKLIST {period.label}")
    print("=" * 17)
    print()
    print("CONTAORDEM")
    print(f"Total no mês: {result.total_month_rows}")
    print(f"Transferências excluídas: {result.transfer_excluded}")
    print(f"Registos elegíveis: {len(result.eligible_rows)}")
    pending_count = len(result.rows_to_validate) if result.rows_to_validate is not None else len(result.internal_results)
    already_audited = len(result.eligible_rows) - pending_count
    if already_audited > 0:
        print(f"Já auditados anteriormente: {already_audited}")
    print(f"Pendentes para validação (AUDITORIA vazia): {pending_count}")
    print()

    entrada_count = sum(1 for item in result.balancete_comparisons if item.tipo == "Entrada")
    saida_count = sum(1 for item in result.balancete_comparisons if item.tipo == "Saída")
    conferred = sum(1 for item in result.balancete_comparisons if item.resultado == "CONFERIDO")
    divergent = len(result.balancete_comparisons) - conferred
    print("BALANCETE")
    print(f"Entradas: {entrada_count}")
    print(f"Saídas: {saida_count}")
    print(f"Planos conferidos: {conferred}")
    print(f"Planos divergentes: {divergent}")
    print()
    print("TIPO | PLANO | CONTAORDEM | BALANCETE | DIFERENÇA | RESULTADO")
    for item in result.balancete_comparisons:
        print(
            " | ".join([
                item.tipo,
                item.plano_conta,
                format_money(item.total_contaordem),
                format_money(item.total_balancete),
                format_money(item.diferenca),
                item.resultado,
            ])
        )
    print()

    fluxo_summary = summarize_fluxo(result.fluxo_results)
    print("FLUXO DE CAIXA")
    print(f"Registos elegíveis: {len(result.fluxo_results)}")
    print(f"Conferidos: {fluxo_summary.get('CONFERIDO', 0)}")
    print(f"Não encontrados: {fluxo_summary.get('NAO_ENCONTRADO', 0)}")
    print(f"Divergentes: {fluxo_summary.get('DIVERGENTE', 0)}")
    print(f"Ambíguos: {fluxo_summary.get('MULTIPLOS_CANDIDATOS', 0)}")
    print()
    print("ID_INTERNO | DATA | TIPO | PLANO | VALOR | DOC_SOMA | RESULTADO | AUDITORIA_PROPOSTA")
    internal_summary = Counter(item.resultado for item in result.internal_results)
    print()
    print("AUDITORIA INTERNA")
    print(f"Registos elegíveis: {len(result.internal_results)}")
    print(f"Conferidos: {internal_summary.get('CONFERIDO', 0)}")
    print(f"Divergentes: {internal_summary.get('DIVERGENTE', 0)}")
    print()
    print("ID_INTERNO | DATA | TIPO | PLANO | VALOR | DOC_SOMA | RESULTADO | AUDITORIA_PROPOSTA")
    for item in result.internal_results:
        if item.resultado == "CONFERIDO":
            continue
        row = item.row
        print(
            " | ".join([
                row.id_interno,
                row.data_mov,
                row.tipo.value,
                row.plano_conta,
                format_money(row.valor),
                row.doc_soma,
                item.resultado,
                item.auditoria_proposta,
            ])
        )


def print_origin_validation_report(result: ContaOrdemOriginValidationRun) -> None:
    label = result.period.label if result.period else "TODOS"
    summary = Counter(item.resultado for item in result.results)
    print(f"VALIDAÇÃO ORIGEM CONTAORDEM - {label}")
    print("=" * 37)
    print(f"Registos avaliados: {result.total_rows}")
    print(f"Validados: {summary.get('VALIDADO', 0)}")
    print(f"Divergentes: {summary.get('DIVERGENTE', 0)}")
    if result.applied:
        print(f"Gravados na coluna ORIGEM: {result.applied}")
    print()
    print("ID_INTERNO | DATA | PROCESSO | DOC_SOMA | RESULTADO | ORIGEM_PROPOSTA")
    for item in result.results:
        if item.resultado == "VALIDADO":
            continue
        row = item.row
        print(
            " | ".join([
                row.id_interno,
                row.data_mov,
                row.processo,
                row.doc_soma,
                item.resultado,
                item.origem_proposta,
            ])
        )


def print_overall_summary(results: List[MonthlyChecklistResult], apply: bool = False) -> None:
    print()
    print("=" * 60)
    print("RESUMO CONSOLIDADO DO PROCESSAMENTO CONTÍNUO")
    print("=" * 60)
    print(f"Períodos analisados: {len(results)}")
    if not results:
        print("Nenhum período foi processado.")
        print("=" * 60)
        return

    periods_str = ", ".join(r.period.label for r in results)
    print(f"Períodos: {periods_str}")
    total_evaluated = sum(len(r.internal_results) for r in results)
    total_conferred = sum(
        sum(1 for item in r.internal_results if item.resultado == "CONFERIDO")
        for r in results
    )
    total_divergent = total_evaluated - total_conferred
    print(f"Total de registos validados: {total_evaluated}")
    print(f"Conferidos: {total_conferred}")
    print(f"Divergentes: {total_divergent}")
    mode_str = "APLICADO NA FOLHA (CONTAORDEM atualizada)" if apply else "DRY-RUN (nenhuma alteração gravada)"
    print(f"Modo: {mode_str}")
    print("=" * 60)
