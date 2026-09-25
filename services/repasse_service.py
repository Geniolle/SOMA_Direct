from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Dict, List, Optional, Sequence

from config.settings import Settings
from core.http_session import ResilientSession


MONTH_NAMES = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}

DIAG_CONFERIDO = "CONFERIDO"
DIAG_AUSENTE_NO_BALANCETE = "AUSENTE_NO_BALANCETE"
DIAG_VALOR_DIVERGENTE = "VALOR_DIVERGENTE"
DIAG_NOME_DIVERGENTE = "NOME_DIVERGENTE"
DIAG_MES_DIVERGENTE = "MES_DIVERGENTE"
DIAG_POSSIVEL_SOMA_DE_LANCAMENTOS = "POSSIVEL_SOMA_DE_LANCAMENTOS"
DIAG_POSSIVEL_AGRUPAMENTO = "POSSIVEL_AGRUPAMENTO"
DIAG_MULTIPLOS_CANDIDATOS = "MULTIPLOS_CANDIDATOS"
DIAG_OUTRO = "OUTRO"


class RepasseParsingError(ValueError):
    """Falha explícita quando o HTML não corresponde ao relatório esperado."""


class SomaSessionExpired(RuntimeError):
    """A sessão autenticada deixou de apontar para um relatório válido."""


@dataclass(frozen=True)
class RepassePeriod:
    ano: int
    mes: int
    mes_nome: str
    data_inicio: str
    data_fim: str


@dataclass(frozen=True)
class RepasseItem:
    period: RepassePeriod
    plano_conta: str
    valor: Decimal
    status_soma: str


@dataclass(frozen=True)
class BalanceteSaidaItem:
    period: RepassePeriod
    plano_conta: str
    valor: Decimal


@dataclass(frozen=True)
class RepasseValidationRecord:
    id_validacao: str
    data_execucao: str
    instituicao: str
    ano: int
    mes: str
    data_inicio: str
    data_fim: str
    plano_conta_repasse: str
    valor_repasse: Optional[Decimal]
    status_repasse_soma: str
    plano_conta_balancete: str
    valor_balancete: Optional[Decimal]
    diferenca: Optional[Decimal]
    resultado_validacao: str
    diagnostico: str
    mes_match_alternativo: str
    observacao: str
    chave_funcional: str


def strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()


def split_cell_lines(html: str) -> List[str]:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    lines = []
    for raw in unescape(text).splitlines():
        clean = " ".join(raw.split()).strip()
        if clean:
            lines.append(clean)
    return lines


def parse_decimal_pt(value: str) -> Decimal:
    text = re.sub(r"[^\d,.-]", "", str(value or "")).strip()
    if not text:
        raise RepasseParsingError("Valor monetário vazio")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise RepasseParsingError(f"Valor monetário inválido: '{value}'") from exc


def format_decimal_pt(value: Optional[Decimal]) -> str:
    if value is None:
        return ""
    return format(value.quantize(Decimal("0.01")), "f").replace(".", ",")


def normalize_plano_conta(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\(\s*-\s*\)\s*", "", text)
    text = " ".join(text.split())
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.casefold()


def find_decimal_subsets(
    items: Sequence[BalanceteSaidaItem],
    target: Decimal,
    *,
    max_size: int = 4,
) -> List[tuple[BalanceteSaidaItem, ...]]:
    matches: List[tuple[BalanceteSaidaItem, ...]] = []
    if len(items) < 2:
        return matches
    import itertools

    for size in range(2, max_size + 1):
        for combo in itertools.combinations(items, size):
            if sum((item.valor for item in combo), Decimal("0.00")) == target:
                matches.append(combo)
                if len(matches) > 1:
                    return matches
    return matches


def functional_key(instituicao: str, ano: int, mes: str, plano_conta: str) -> str:
    return "|".join(
        [
            str(instituicao or "").strip().casefold(),
            str(ano),
            str(mes or "").strip().casefold(),
            normalize_plano_conta(plano_conta),
        ]
    )


def month_period(ano: int, mes: int) -> RepassePeriod:
    last_day = calendar.monthrange(ano, mes)[1]
    return RepassePeriod(
        ano=ano,
        mes=mes,
        mes_nome=MONTH_NAMES[mes],
        data_inicio=date(ano, mes, 1).strftime("%d/%m/%Y"),
        data_fim=date(ano, mes, last_day).strftime("%d/%m/%Y"),
    )


def month_number(name: str) -> int:
    target = normalize_plano_conta(name)
    for number, month_name in MONTH_NAMES.items():
        if normalize_plano_conta(month_name) == target:
            return number
    raise RepasseParsingError(f"Mês não reconhecido no relatório de repasses: '{name}'")


def extract_tables(html: str, class_name: str = "export_tabela") -> List[str]:
    pattern = (
        r"<table\b(?=[^>]*class=[\"'][^\"']*\b"
        + re.escape(class_name)
        + r"\b[^\"']*[\"'])[^>]*>(.*?)</table>"
    )
    return re.findall(pattern, html or "", flags=re.IGNORECASE | re.DOTALL)


def extract_rows(table_html: str) -> List[List[str]]:
    rows: List[List[str]] = []
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_html, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
        rows.append(cells)
    return rows


def ensure_report_html(html: str, expected_marker: str) -> None:
    body = html or ""
    normalized = normalize_plano_conta(strip_tags(body))
    login_markers = ("senha", "login", "buscauser", "usuario", "utilizador")
    if any(marker in normalized for marker in login_markers) and expected_marker not in normalized:
        raise SomaSessionExpired("A resposta parece ser uma página de login, não um relatório SOMA")
    if "export_tabela" not in body:
        raise SomaSessionExpired("Relatório SOMA sem tabela export_tabela; sessão expirada ou HTML inesperado")


class RepasseReportService:
    """Acesso read-only aos relatórios de Repasses e Balancete do SOMA."""

    def __init__(self, settings: Settings, http: ResilientSession):
        self.settings = settings
        self.http = http
        self.base_url = settings.site_base_url.rstrip("/") + "/"

    def fetch_repasse_html(self, ano: int, instituicao_id: Optional[str] = None) -> str:
        url = f"{self.base_url}sys/relatorios/relatorios_repasse.php"
        response = self.http.get(
            url,
            params={
                "i": instituicao_id or self.settings.institution_id,
                "t": "1",
                "a_s": str(ano),
                "p": "undefined",
                "t_i": "undefined",
                "end": "undefined",
                "s": "undefined",
                "t_comp": "undefined",
            },
        )
        ensure_report_html(response.text, "repasse")
        return response.text

    def fetch_balancete_html(self, period: RepassePeriod, instituicao_id: Optional[str] = None) -> str:
        url = f"{self.base_url}sys/relatorios/relatorios_balancete.php"
        response = self.http.get(
            url,
            params={
                "i": instituicao_id or self.settings.institution_id,
                "c": period.data_inicio,
                "f": period.data_fim,
                "t": "0",
                "p": "5",
                "id_cc": "null",
                "id_c": "null",
                "id_m": "2",
            },
        )
        ensure_report_html(response.text, "valor saida total")
        return response.text

    @staticmethod
    def parse_repasse_html(html: str, ano: int) -> List[RepasseItem]:
        ensure_report_html(html, "repasse")
        tables = extract_tables(html)
        if not tables:
            raise RepasseParsingError("Nenhuma table.export_tabela encontrada no relatório de repasses")

        items: List[RepasseItem] = []
        for table in tables:
            for cells in extract_rows(table):
                if len(cells) < 4:
                    continue
                first = strip_tags(cells[0])
                first_norm = normalize_plano_conta(first)
                row_text = normalize_plano_conta(" ".join(strip_tags(cell) for cell in cells))
                if not first or "mes" == first_norm or "mês" == first_norm:
                    continue
                if "total" in first_norm or "export" in row_text:
                    continue
                try:
                    period = month_period(ano, month_number(first))
                except RepasseParsingError:
                    continue

                planos = split_cell_lines(cells[1])
                valores = split_cell_lines(cells[2])
                statuses = split_cell_lines(cells[3])
                if not (len(planos) == len(valores) == len(statuses)):
                    raise RepasseParsingError(
                        "Quantidade inconsistente em repasses "
                        f"{period.mes_nome}/{ano}: planos={len(planos)}, "
                        f"valores={len(valores)}, statuses={len(statuses)}"
                    )
                for plano, valor, status in zip(planos, valores, statuses):
                    if normalize_plano_conta(plano).startswith("total"):
                        continue
                    items.append(
                        RepasseItem(
                            period=period,
                            plano_conta=plano,
                            valor=parse_decimal_pt(valor),
                            status_soma=status,
                        )
                    )

        if not items:
            raise RepasseParsingError("Nenhum item mensal encontrado no relatório de repasses")
        return items

    @staticmethod
    def parse_balancete_html(html: str, period: RepassePeriod) -> List[BalanceteSaidaItem]:
        ensure_report_html(html, "valor saida total")
        candidate_table = None
        for table in extract_tables(html):
            text = normalize_plano_conta(strip_tags(table))
            if "valor saida total" in text or "total saida" in text:
                candidate_table = table
                break
        if candidate_table is None:
            raise RepasseParsingError("Tabela da secção SAÍDAS não encontrada no balancete")

        rows = extract_rows(candidate_table)
        if not rows:
            raise RepasseParsingError("Tabela SAÍDAS do balancete sem linhas")

        items: List[BalanceteSaidaItem] = []
        for cells in rows:
            plain_cells = [strip_tags(cell) for cell in cells]
            row_text = normalize_plano_conta(" ".join(plain_cells))
            if not plain_cells or "valor saida total" in row_text or "plano" in row_text and "valor" in row_text:
                continue
            if "total saida" in row_text:
                continue
            if len(plain_cells) < 2:
                continue
            plano = plain_cells[0]
            value_cell = plain_cells[-1]
            if not plano or normalize_plano_conta(plano).startswith("total"):
                continue
            items.append(
                BalanceteSaidaItem(
                    period=period,
                    plano_conta=plano,
                    valor=parse_decimal_pt(value_cell),
                )
            )

        if not items:
            raise RepasseParsingError("Nenhum lançamento de SAÍDAS encontrado no balancete")
        return items


def reconcile_repasse_items(
    repasse_items: List[RepasseItem],
    balancete_by_month: Dict[int, List[BalanceteSaidaItem]],
    *,
    instituicao: str,
    data_execucao: str,
    existing_ids: Optional[Dict[str, str]] = None,
) -> List[RepasseValidationRecord]:
    existing_ids = existing_ids or {}
    records: List[RepasseValidationRecord] = []
    sequence_by_month: Dict[int, int] = {}
    for item in repasse_items:
        candidates = [
            bal
            for bal in balancete_by_month.get(item.period.mes, [])
            if normalize_plano_conta(bal.plano_conta) == normalize_plano_conta(item.plano_conta)
            and bal.valor == item.valor
        ]
        key = functional_key(instituicao, item.period.ano, item.period.mes_nome, item.plano_conta)
        sequence_by_month[item.period.mes] = sequence_by_month.get(item.period.mes, 0) + 1
        record_id = existing_ids.get(
            key,
            f"REP-{item.period.ano}-{item.period.mes:02d}-{sequence_by_month[item.period.mes]:03d}",
        )

        if len(candidates) == 1:
            matched = candidates[0]
            resultado = "CONFERIDO"
            diagnostico = DIAG_CONFERIDO
            mes_match_alternativo = ""
            observacao = "Correspondência exata encontrada no Balancete"
            valor_balancete: Optional[Decimal] = matched.valor
            plano_balancete = matched.plano_conta
        elif len(candidates) > 1:
            matched = candidates[0]
            resultado = "REPASSE INCOMPLETO"
            diagnostico = DIAG_MULTIPLOS_CANDIDATOS
            mes_match_alternativo = ""
            observacao = f"Correspondência ambígua: {len(candidates)} candidatos idênticos no Balancete"
            valor_balancete = matched.valor
            plano_balancete = matched.plano_conta
        else:
            same_month_items = balancete_by_month.get(item.period.mes, [])
            same_plan = [
                bal
                for bal in same_month_items
                if normalize_plano_conta(bal.plano_conta) == normalize_plano_conta(item.plano_conta)
            ]
            alternative_matches = [
                bal
                for month, month_items in balancete_by_month.items()
                if month != item.period.mes
                for bal in month_items
                if normalize_plano_conta(bal.plano_conta) == normalize_plano_conta(item.plano_conta)
                and bal.valor == item.valor
            ]
            same_value = [
                bal
                for bal in same_month_items
                if bal.valor == item.valor
            ]
            same_plan_sums = find_decimal_subsets(same_plan, item.valor)
            any_sums = find_decimal_subsets(same_month_items, item.valor)
            resultado = "REPASSE INCOMPLETO"
            if same_plan:
                matched = same_plan[0]
                valor_balancete = matched.valor
                plano_balancete = matched.plano_conta
                diagnostico = DIAG_VALOR_DIVERGENTE
                mes_match_alternativo = ""
                observacao = "Mesmo plano encontrado no mês, mas com valor diferente."
            elif len(alternative_matches) == 1:
                matched = alternative_matches[0]
                valor_balancete = matched.valor
                plano_balancete = matched.plano_conta
                diagnostico = DIAG_MES_DIVERGENTE
                mes_match_alternativo = matched.period.mes_nome
                observacao = f"Match exato encontrado em outro mês: {matched.period.mes_nome}"
            elif len(alternative_matches) > 1:
                matched = alternative_matches[0]
                valor_balancete = matched.valor
                plano_balancete = matched.plano_conta
                diagnostico = DIAG_MULTIPLOS_CANDIDATOS
                mes_match_alternativo = ""
                months = ", ".join(sorted({match.period.mes_nome for match in alternative_matches}))
                observacao = f"Múltiplos matches alternativos encontrados em outros meses: {months}"
            elif len(same_plan_sums) == 1:
                combo = same_plan_sums[0]
                valor_balancete = sum((bal.valor for bal in combo), Decimal("0.00"))
                plano_balancete = " + ".join(bal.plano_conta for bal in combo)
                diagnostico = DIAG_POSSIVEL_SOMA_DE_LANCAMENTOS
                mes_match_alternativo = ""
                observacao = "Valor do repasse corresponde à soma de saídas do mesmo plano no mês."
            elif len(same_plan_sums) > 1:
                combo = same_plan_sums[0]
                valor_balancete = sum((bal.valor for bal in combo), Decimal("0.00"))
                plano_balancete = " + ".join(bal.plano_conta for bal in combo)
                diagnostico = DIAG_MULTIPLOS_CANDIDATOS
                mes_match_alternativo = ""
                observacao = "Múltiplas somas possíveis de lançamentos do mesmo plano."
            elif same_value:
                matched = same_value[0]
                valor_balancete = matched.valor
                plano_balancete = matched.plano_conta
                diagnostico = DIAG_NOME_DIVERGENTE
                mes_match_alternativo = ""
                observacao = "Mesmo valor encontrado no mês com plano de conta diferente."
            elif len(any_sums) == 1:
                combo = any_sums[0]
                valor_balancete = sum((bal.valor for bal in combo), Decimal("0.00"))
                plano_balancete = " + ".join(bal.plano_conta for bal in combo)
                diagnostico = DIAG_POSSIVEL_SOMA_DE_LANCAMENTOS
                mes_match_alternativo = ""
                observacao = "Valor do repasse corresponde à soma de saídas no mês."
            elif len(any_sums) > 1:
                combo = any_sums[0]
                valor_balancete = sum((bal.valor for bal in combo), Decimal("0.00"))
                plano_balancete = " + ".join(bal.plano_conta for bal in combo)
                diagnostico = DIAG_MULTIPLOS_CANDIDATOS
                mes_match_alternativo = ""
                observacao = "Múltiplas somas possíveis de lançamentos no mês."
            else:
                valor_balancete = None
                plano_balancete = ""
                diagnostico = DIAG_AUSENTE_NO_BALANCETE
                mes_match_alternativo = ""
                observacao = "Sem correspondência exata no Balancete"

        diferenca = None if valor_balancete is None else valor_balancete - item.valor
        records.append(
            RepasseValidationRecord(
                id_validacao=record_id,
                data_execucao=data_execucao,
                instituicao=instituicao,
                ano=item.period.ano,
                mes=item.period.mes_nome,
                data_inicio=item.period.data_inicio,
                data_fim=item.period.data_fim,
                plano_conta_repasse=item.plano_conta,
                valor_repasse=item.valor,
                status_repasse_soma=item.status_soma,
                plano_conta_balancete=plano_balancete,
                valor_balancete=valor_balancete,
                diferenca=diferenca,
                resultado_validacao=resultado,
                diagnostico=diagnostico,
                mes_match_alternativo=mes_match_alternativo,
                observacao=observacao,
                chave_funcional=key,
            )
        )
    return records
