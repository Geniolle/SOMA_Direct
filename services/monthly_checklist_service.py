from __future__ import annotations

import calendar
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config.settings import Settings
from core.http_session import ResilientSession
from domain.models import ContaOrdemRow, TipoMovimento, is_entrada_ou_saida, normalize_date_str
from services.repasse_service import extract_rows, extract_tables, parse_decimal_pt, strip_tags


RESULT_CONFERIDO = "CONFERIDO"
RESULT_DIVERGENTE = "DIVERGENTE"
RESULT_AUSENTE_BALANCETE = "AUSENTE_NO_BALANCETE"
RESULT_AUSENTE_CONTAORDEM = "AUSENTE_NA_CONTAORDEM"

FLOW_CONFERIDO = "Conferido"
FLOW_NAO_ENCONTRADO = "Erro: registo não encontrado no Fluxo de Caixa"
FLOW_VALOR_DIVERGENTE = "Erro: valor divergente no Fluxo de Caixa"
FLOW_PLANO_DIVERGENTE = "Erro: Plano de Conta divergente no Fluxo de Caixa"
FLOW_TIPO_DIVERGENTE = "Erro: tipo divergente no Fluxo de Caixa"
FLOW_DATA_DIVERGENTE = "Erro: data divergente no Fluxo de Caixa"
FLOW_MULTIPLOS = "Erro: múltiplos candidatos no Fluxo de Caixa"


@dataclass(frozen=True)
class ChecklistPeriod:
    ano: int
    mes: int
    data_inicio: str
    data_fim: str

    @property
    def label(self) -> str:
        return f"{self.mes:02d}/{self.ano}"


@dataclass(frozen=True)
class BalancetePlanTotal:
    tipo: str
    plano: str
    valor: Decimal


@dataclass(frozen=True)
class ContaOrdemChecklistRow:
    row_number: int
    data_mov: str
    tipo: TipoMovimento
    plano_conta: str
    valor: Decimal
    doc_soma: str
    centro_custo: str
    descricao: str
    descricao_soma: str
    caixa: str
    forma_pagamento: str
    id_interno: str
    processo: str
    auditoria: str
    raw: Dict[str, Any]


@dataclass(frozen=True)
class BalanceteComparison:
    tipo: str
    plano_conta: str
    total_contaordem: Optional[Decimal]
    total_balancete: Optional[Decimal]
    diferenca: Optional[Decimal]
    resultado: str


@dataclass(frozen=True)
class FluxoCaixaItem:
    descricao: str
    plano_conta: str
    plano_contabil: str
    centro_custo: str
    caixa: str
    fornecedor: str
    nf: str
    data_vencimento_entrada: str
    data_baixa: str
    valor: Decimal

    @property
    def tipo(self) -> TipoMovimento:
        return TipoMovimento.ENTRADA if self.valor >= Decimal("0.00") else TipoMovimento.SAIDA

    @property
    def valor_abs(self) -> Decimal:
        return abs(self.valor)


@dataclass(frozen=True)
class FluxoMatchResult:
    row: ContaOrdemChecklistRow
    resultado: str
    auditoria_proposta: str
    candidatos: Tuple[FluxoCaixaItem, ...] = ()


@dataclass(frozen=True)
class InternalAuditResult:
    row: ContaOrdemChecklistRow
    resultado: str
    auditoria_proposta: str


def normalize_plan(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\(\s*-\s*\)\s*", "", text)
    text = " ".join(text.split())
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


def normalize_text(value: str) -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


def parse_decimal_money(value: Any) -> Decimal:
    text = str(value or "").strip()
    if not text:
        return Decimal("0.00")
    return parse_decimal_pt(text)


def format_money(value: Optional[Decimal]) -> str:
    if value is None:
        return ""
    return format(value.quantize(Decimal("0.01")), "f").replace(".", ",")


def month_period(ano: int, mes: int) -> ChecklistPeriod:
    last_day = calendar.monthrange(ano, mes)[1]
    return ChecklistPeriod(
        ano=ano,
        mes=mes,
        data_inicio=date(ano, mes, 1).strftime("%d/%m/%Y"),
        data_fim=date(ano, mes, last_day).strftime("%d/%m/%Y"),
    )


def parse_date_key(value: Any) -> Optional[date]:
    text = normalize_date_str(value)
    if not text:
        return None
    try:
        day, month, year = [int(part) for part in text.split("/")]
        return date(year, month, day)
    except Exception:
        return None


def is_transferencia(tipo: Any) -> bool:
    return normalize_text(str(tipo or "")) == "transferencia"


def row_to_checklist(row: ContaOrdemRow) -> ContaOrdemChecklistRow:
    return ContaOrdemChecklistRow(
        row_number=row.row_number,
        data_mov=normalize_date_str(row.data_mov),
        tipo=row.tipo,
        plano_conta=row.plano_conta,
        valor=parse_decimal_money(row.importancia),
        doc_soma=row.doc_soma,
        centro_custo=row.centro_custo,
        descricao=row.descricao,
        descricao_soma=row.descricao_soma,
        caixa=row.caixa,
        forma_pagamento=row.forma_pagamento,
        id_interno=row.id_interno,
        processo=row.processo,
        auditoria=row.auditoria,
        raw=row.raw,
    )


def filter_month_rows(rows: Iterable[ContaOrdemRow], period: ChecklistPeriod) -> Tuple[List[ContaOrdemChecklistRow], int]:
    selected: List[ContaOrdemChecklistRow] = []
    transfer_count = 0
    start = parse_date_key(period.data_inicio)
    end = parse_date_key(period.data_fim)
    for row in rows:
        row_date = parse_date_key(row.data_mov)
        if not row_date or row_date < start or row_date > end:
            continue
        if row.tipo == TipoMovimento.TRANSFERENCIA or is_transferencia(row.tipo.value):
            transfer_count += 1
            continue
        if row.tipo not in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA):
            continue
        selected.append(row_to_checklist(row))
    return selected, transfer_count


def is_empty_auditoria(value: Any) -> bool:
    return not str(value or "").strip()


def find_first_period(
    rows: Iterable[ContaOrdemRow],
    only_empty_auditoria: bool = True,
    exclude_periods: Optional[set[str]] = None,
) -> Tuple[Optional[ChecklistPeriod], int]:
    rows = list(rows)
    if only_empty_auditoria:
        pending_rows = [
            row for row in rows
            if is_empty_auditoria(row.auditoria) and is_entrada_ou_saida(row.tipo)
        ]
        if not pending_rows:
            pending_rows = [
                row for row in rows
                if is_empty_auditoria(row.auditoria)
                and not (row.tipo == TipoMovimento.TRANSFERENCIA or is_transferencia(getattr(row.tipo, "value", str(row.tipo))))
            ]
    else:
        pending_rows = [row for row in rows if is_entrada_ou_saida(row.tipo)]
        if not pending_rows:
            pending_rows = rows

    dates = [parse_date_key(row.data_mov) for row in pending_rows]
    valid_dates = [item for item in dates if item is not None]
    if exclude_periods:
        valid_dates = [
            item for item in valid_dates
            if f"{item.month:02d}/{item.year}" not in exclude_periods
        ]
    if not valid_dates:
        return None, 0
    first = min(valid_dates)
    first_month_count = sum(
        1
        for item in valid_dates
        if item.year == first.year and item.month == first.month
    )
    return month_period(first.year, first.month), first_month_count


def aggregate_contaordem(rows: Iterable[ContaOrdemChecklistRow]) -> Dict[Tuple[str, str], Decimal]:
    totals: Dict[Tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0.00"))
    for row in rows:
        tipo = "Entrada" if row.tipo == TipoMovimento.ENTRADA else "Saída"
        totals[(tipo, normalize_plan(row.plano_conta))] += row.valor
    return dict(totals)


def representative_plans(rows: Iterable[ContaOrdemChecklistRow]) -> Dict[Tuple[str, str], str]:
    out: Dict[Tuple[str, str], str] = {}
    for row in rows:
        tipo = "Entrada" if row.tipo == TipoMovimento.ENTRADA else "Saída"
        out.setdefault((tipo, normalize_plan(row.plano_conta)), row.plano_conta)
    return out


def parse_balancete_html(html: str) -> List[BalancetePlanTotal]:
    items: List[BalancetePlanTotal] = []
    for table in extract_tables(html):
        text = normalize_text(strip_tags(table))
        if "valor entrada total" in text:
            items.extend(_parse_balancete_table(table, "Entrada"))
        if "valor saida total" in text:
            items.extend(_parse_balancete_table(table, "Saída"))
    return items


def _parse_balancete_table(table_html: str, tipo: str) -> List[BalancetePlanTotal]:
    out: List[BalancetePlanTotal] = []
    for cells in extract_rows(table_html):
        plain = [strip_tags(cell) for cell in cells]
        row_text = normalize_text(" ".join(plain))
        if not plain or "valor entrada total" in row_text or "valor saida total" in row_text:
            continue
        if "total entrada" in row_text or "total saida" in row_text:
            continue
        if len(plain) < 2:
            continue
        plano = plain[0].strip()
        if not plano:
            continue
        out.append(BalancetePlanTotal(tipo=tipo, plano=plano, valor=parse_decimal_money(plain[-1])))
    return out


def compare_balancete(
    conta_totals: Dict[Tuple[str, str], Decimal],
    conta_plans: Dict[Tuple[str, str], str],
    balancete_items: Iterable[BalancetePlanTotal],
) -> List[BalanceteComparison]:
    bal_totals: Dict[Tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0.00"))
    bal_plans: Dict[Tuple[str, str], str] = {}
    for item in balancete_items:
        key = (item.tipo, normalize_plan(item.plano))
        bal_totals[key] += item.valor
        bal_plans.setdefault(key, item.plano)

    out: List[BalanceteComparison] = []
    for key in sorted(set(conta_totals) | set(bal_totals)):
        conta_val = conta_totals.get(key)
        bal_val = bal_totals.get(key)
        plano = conta_plans.get(key) or bal_plans.get(key) or key[1]
        if conta_val is None:
            diff = None
            result = RESULT_AUSENTE_CONTAORDEM
        elif bal_val is None:
            diff = None
            result = RESULT_AUSENTE_BALANCETE
        else:
            diff = conta_val - bal_val
            result = RESULT_CONFERIDO if diff == Decimal("0.00") else RESULT_DIVERGENTE
        out.append(BalanceteComparison(key[0], plano, conta_val, bal_val, diff, result))
    return out


def parse_fluxo_caixa_html(html: str) -> List[FluxoCaixaItem]:
    movement_table = None
    for table in extract_tables(html):
        text = normalize_text(strip_tags(table))
        if "movimentacao do" in text and "plano de contas" in text and "data baixa" in text:
            movement_table = table
            break
    if movement_table is None:
        return []

    rows = extract_rows(movement_table)
    items: List[FluxoCaixaItem] = []
    for cells in rows:
        plain = [strip_tags(cell) for cell in cells]
        if len(plain) < 10:
            continue
        row_text = normalize_text(" ".join(plain))
        if "descricao" in row_text and "plano de contas" in row_text:
            continue
        try:
            value = parse_decimal_money(plain[9])
        except Exception:
            continue
        items.append(
            FluxoCaixaItem(
                descricao=plain[0],
                plano_conta=plain[1],
                plano_contabil=plain[2],
                centro_custo=plain[3],
                caixa=plain[4],
                fornecedor=plain[5],
                nf=plain[6],
                data_vencimento_entrada=normalize_date_str(plain[7]),
                data_baixa=normalize_date_str(plain[8]),
                valor=value,
            )
        )
    return items


def match_fluxo_rows(
    rows: Iterable[ContaOrdemChecklistRow],
    fluxo_items: Iterable[FluxoCaixaItem],
) -> List[FluxoMatchResult]:
    fluxo = list(fluxo_items)
    results: List[FluxoMatchResult] = []
    used_ids: set[int] = set()
    for row in rows:
        candidates = [
            item for item in fluxo
            if item.tipo == row.tipo
            and item.data_baixa == row.data_mov
            and normalize_plan(item.plano_conta) == normalize_plan(row.plano_conta)
            and item.valor_abs == row.valor
        ]
        available = [item for item in candidates if id(item) not in used_ids]
        candidates = available or candidates

        if len(candidates) == 1:
            item = candidates[0]
            used_ids.add(id(item))
            results.append(FluxoMatchResult(row, "CONFERIDO", FLOW_CONFERIDO, (item,)))
            continue
        if len(candidates) > 1:
            narrowed = _narrow_fluxo_candidates(row, candidates)
            if len(narrowed) == 1:
                item = narrowed[0]
                used_ids.add(id(item))
                results.append(FluxoMatchResult(row, "CONFERIDO", FLOW_CONFERIDO, (item,)))
            else:
                results.append(FluxoMatchResult(row, "MULTIPLOS_CANDIDATOS", FLOW_MULTIPLOS, tuple(candidates)))
            continue

        same_date_type_value = [
            item for item in fluxo
            if item.tipo == row.tipo and item.data_baixa == row.data_mov and item.valor_abs == row.valor
        ]
        if same_date_type_value:
            results.append(FluxoMatchResult(row, "DIVERGENTE", FLOW_PLANO_DIVERGENTE, tuple(same_date_type_value)))
            continue

        same_date_plan = [
            item for item in fluxo
            if item.tipo == row.tipo and item.data_baixa == row.data_mov and normalize_plan(item.plano_conta) == normalize_plan(row.plano_conta)
        ]
        if same_date_plan:
            results.append(FluxoMatchResult(row, "DIVERGENTE", FLOW_VALOR_DIVERGENTE, tuple(same_date_plan)))
            continue

        same_plan_value = [
            item for item in fluxo
            if item.tipo == row.tipo and normalize_plan(item.plano_conta) == normalize_plan(row.plano_conta) and item.valor_abs == row.valor
        ]
        if same_plan_value:
            results.append(FluxoMatchResult(row, "DIVERGENTE", FLOW_DATA_DIVERGENTE, tuple(same_plan_value)))
            continue

        opposite_type = [
            item for item in fluxo
            if item.data_baixa == row.data_mov and normalize_plan(item.plano_conta) == normalize_plan(row.plano_conta) and item.valor_abs == row.valor
        ]
        if opposite_type:
            results.append(FluxoMatchResult(row, "DIVERGENTE", FLOW_TIPO_DIVERGENTE, tuple(opposite_type)))
            continue

        results.append(FluxoMatchResult(row, "NAO_ENCONTRADO", FLOW_NAO_ENCONTRADO, ()))
    return results


def _narrow_fluxo_candidates(row: ContaOrdemChecklistRow, candidates: List[FluxoCaixaItem]) -> List[FluxoCaixaItem]:
    narrowed = candidates
    description = normalize_text(row.descricao_soma or row.descricao)
    if description:
        by_desc = [item for item in narrowed if normalize_text(item.descricao) == description]
        if by_desc:
            narrowed = by_desc
    if row.centro_custo:
        by_cc = [item for item in narrowed if normalize_text(item.centro_custo) == normalize_text(row.centro_custo)]
        if by_cc:
            narrowed = by_cc
    if row.caixa:
        row_caixa = normalize_text(row.caixa)
        by_caixa = [item for item in narrowed if row_caixa in normalize_text(item.caixa) or normalize_text(item.caixa) in row_caixa]
        if by_caixa:
            narrowed = by_caixa
    return narrowed


def summarize_fluxo(results: Iterable[FluxoMatchResult]) -> Counter:
    return Counter(result.resultado for result in results)


def pick_first_value(raw: Dict[str, Any], names: Iterable[str]) -> str:
    normalized = {normalize_text(key).replace("_", " "): value for key, value in raw.items()}
    for name in names:
        value = normalized.get(normalize_text(name).replace("_", " "))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_doc(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    return text


def build_records_by_id(records: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        id_value = pick_first_value(record, ("ID_INTERNO", "ID INTERNO"))
        if id_value:
            out[id_value].append(record)
    return dict(out)


def build_soma_by_doc(records: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        code = normalize_doc(pick_first_value(record, ("CODIGO", "CÓDIGO")))
        if code:
            out[code].append(record)
    return dict(out)


def validate_internal_rows(
    rows: Iterable[ContaOrdemChecklistRow],
    source_records_by_process: Dict[str, List[Dict[str, Any]]],
    soma_records: List[Dict[str, Any]],
) -> List[InternalAuditResult]:
    source_by_process = {
        normalize_text(process): build_records_by_id(records)
        for process, records in source_records_by_process.items()
    }
    soma_by_doc = build_soma_by_doc(soma_records)
    results: List[InternalAuditResult] = []

    for row in rows:
        errors: List[str] = []
        process_key = normalize_text(row.processo)
        if not row.processo:
            errors.append("PROCESSO vazio na CONTAORDEM")
        if not row.id_interno:
            errors.append("ID_INTERNO vazio na CONTAORDEM")

        source_record = None
        if process_key and row.id_interno:
            matches = source_by_process.get(process_key, {}).get(row.id_interno, [])
            if len(matches) == 1:
                source_record = matches[0]
            elif len(matches) > 1:
                errors.append(f"ID_INTERNO duplicado na origem {row.processo}: {row.id_interno}")
            else:
                errors.append(f"ID_INTERNO não encontrado na origem {row.processo}: {row.id_interno}")

        if source_record is not None:
            source_date = normalize_date_str(pick_first_value(source_record, ("DATA MOV.", "DATA", "PAGAMENTO")))
            if source_date and source_date != row.data_mov:
                errors.append(f"Data divergente na origem: origem={source_date} contaordem={row.data_mov}")
            elif not source_date:
                errors.append("Data ausente na origem")

            source_amount_raw = pick_first_value(source_record, ("IMPORTÂNCIA", "VALOR", "VALOR DA COMPRA", "MONTANTE", "VALOR A PAGAR"))
            try:
                source_amount = abs(parse_decimal_money(source_amount_raw))
                if source_amount != row.valor:
                    errors.append(f"Valor divergente na origem: origem={format_money(source_amount)} contaordem={format_money(row.valor)}")
            except Exception:
                errors.append(f"Valor inválido/ausente na origem: {source_amount_raw}")

            source_doc = normalize_doc(pick_first_value(source_record, ("DOC. SOMA", "DOC SOMA", "CODIGO", "CÓDIGO")))
            row_doc = normalize_doc(row.doc_soma)
            if source_doc != row_doc:
                errors.append(f"DOC.SOMA divergente na origem: origem={source_doc or 'vazio'} contaordem={row_doc or 'vazio'}")

        row_doc = normalize_doc(row.doc_soma)
        if not row_doc:
            errors.append("DOC.SOMA vazio na CONTAORDEM")
        else:
            soma_matches = soma_by_doc.get(row_doc, [])
            if len(soma_matches) == 1:
                soma_record = soma_matches[0]
                soma_desc = pick_first_value(soma_record, ("DESCRIÇÃO", "DESCRICAO"))
                expected_desc = row.descricao_soma or row.descricao
                if normalize_text(soma_desc) != normalize_text(expected_desc):
                    errors.append(f"Descrição SOMA divergente: SOMA='{soma_desc}' CONTAORDEM='{expected_desc}'")
                soma_date = normalize_date_str(pick_first_value(soma_record, ("PAGAMENTO", "DATA")))
                if soma_date != row.data_mov:
                    errors.append(f"Data divergente na sheet SOMA: SOMA={soma_date or 'vazio'} contaordem={row.data_mov}")
                soma_value_raw = pick_first_value(soma_record, ("VALOR", "IMPORTÂNCIA"))
                try:
                    soma_value = abs(parse_decimal_money(soma_value_raw))
                    if soma_value != row.valor:
                        errors.append(f"Valor divergente na sheet SOMA: SOMA={format_money(soma_value)} contaordem={format_money(row.valor)}")
                except Exception:
                    errors.append(f"Valor inválido/ausente na sheet SOMA: {soma_value_raw}")
            elif len(soma_matches) > 1:
                errors.append(f"DOC.SOMA duplicado na sheet SOMA: {row_doc}")
            else:
                errors.append(f"DOC.SOMA não encontrado na sheet SOMA: {row_doc}")

        if errors:
            results.append(InternalAuditResult(row, "DIVERGENTE", "Erro: " + "; ".join(errors)))
        else:
            results.append(InternalAuditResult(row, "CONFERIDO", "Conferido"))

    return results


class MonthlyChecklistSomaReports:
    def __init__(self, settings: Settings, http: ResilientSession):
        self.settings = settings
        self.http = http
        self.base_url = settings.site_base_url.rstrip("/") + "/"

    def fetch_balancete_html(self, period: ChecklistPeriod) -> str:
        response = self.http.get(
            f"{self.base_url}sys/relatorios/relatorios_balancete.php",
            params={
                "i": self.settings.institution_id,
                "c": period.data_inicio,
                "f": period.data_fim,
                "t": "0",
                "p": "5",
                "id_cc": "null",
                "id_c": "null",
                "id_m": "2",
            },
        )
        return response.text

    def fetch_fluxo_caixa_html(self, period: ChecklistPeriod) -> str:
        response = self.http.get(
            f"{self.base_url}sys/relatorios/relatorios_fluxoCaixa.php",
            params={
                "t": "2",
                "ft": "0,1,2,3,4,5,6",
                "i": self.settings.institution_id,
                "c": period.data_inicio,
                "f": period.data_fim,
                "id_c": "null",
                "t_d": "1",
                "id_cc": "null",
                "id_m": "2",
                "o": "0",
            },
        )
        return response.text
