from __future__ import annotations

import logging
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gspread
from gspread.utils import ValueInputOption, ValueRenderOption

from config.settings import Settings
from domain.models import (
    ContaOrdemRow,
    TipoMovimento,
    is_entrada_ou_saida,
    norm_basic,
    normalize_document_value,
    strip_date_prefix,
    strip_suffix_n,
)
from services.repasse_service import (
    RepasseValidationRecord,
    format_decimal_pt,
    functional_key,
)

logger = logging.getLogger("soma_direct.sheets")

@dataclass(frozen=True)
class OriginConfig:
    processo: str
    sheet_name: str
    spreadsheet_url: Optional[str] = None
    id_column_name: str = "ID_INTERNO"
    doc_column_name: str = "DOC. SOMA"


EXTERNAL_SOURCE_SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "11sUHhTzKaV21uX_FpBOEnJxNpFjUn6EHEiU79Pe3jXU/edit"
)


def build_default_origin_registry(
    default_spreadsheet_url: Optional[str] = None,
    app_verbo_cafe_url: Optional[str] = None,
) -> Dict[str, OriginConfig]:
    verbo_cafe_url = app_verbo_cafe_url or EXTERNAL_SOURCE_SPREADSHEET_URL
    configs = [
        OriginConfig(processo="T_EXTRATO", sheet_name="T_EXTRATO", spreadsheet_url=default_spreadsheet_url),
        OriginConfig(processo="DÍZIMOS/OFERTAS", sheet_name="DÍZIMOS/OFERTAS", spreadsheet_url=default_spreadsheet_url),
        OriginConfig(processo="SAÍDAS", sheet_name="SAÍDAS", spreadsheet_url=default_spreadsheet_url),
        OriginConfig(processo="Financeiro", sheet_name="Financeiro", spreadsheet_url=verbo_cafe_url),
        OriginConfig(processo="VC_VENDAS", sheet_name="VC_VENDAS", spreadsheet_url=verbo_cafe_url),
    ]
    registry: Dict[str, OriginConfig] = {}
    for cfg in configs:
        registry[norm_basic(cfg.processo)] = cfg
    return registry


DEFAULT_ORIGIN_REGISTRY = build_default_origin_registry()

SOURCE_SHEETS = {
    norm_basic("T_EXTRATO"): "T_EXTRATO",
    norm_basic("DÍZIMOS/OFERTAS"): "DÍZIMOS/OFERTAS",
    norm_basic("SAÍDAS"): "SAÍDAS",
    norm_basic("Financeiro"): "Financeiro",
    norm_basic("VC_VENDAS"): "VC_VENDAS",
}
EXTERNAL_SOURCE_SHEETS = {"Financeiro", "VC_VENDAS"}


def _normalize_header(name: str) -> str:
    cleaned = norm_basic(name).replace("_", " ").replace(".", " ")
    return " ".join(cleaned.split())

# O rótulo do card no site nem sempre bate com o nome da coluna na sheet
# GERENCIAR CAIXAS (ex.: o site chama de "CAIXA ECONÓMICA MONTEPIO GERAL - CC",
# a sheet usa historicamente "CAIXA BANCO" para essa mesma conta).
CAIXAS_LABEL_ALIASES = {
    norm_basic("CAIXA ECONÓMICA MONTEPIO GERAL - CC"): "CAIXA BANCO",
}

# Colunas obrigatórias da sheet SOMA e o atributo correspondente em SomaSearchResult.
SOMA_REPORT_COLUMNS = {
    "CODIGO": "codigo",
    "TIPO": "tipo",
    "DESCRIÇÃO": "descricao",
    "VALOR": "valor",
    "PAGAMENTO": "data",
    "STATUS": "status",
    "BAIXA": "baixa",
}

REPASSE_HEADERS = [
    "ID_VALIDACAO",
    "DATA_EXECUCAO",
    "INSTITUICAO",
    "ANO",
    "MES",
    "DATA_INICIO",
    "DATA_FIM",
    "PLANO_CONTA_REPASSE",
    "VALOR_REPASSE",
    "STATUS_REPASSE_SOMA",
    "PLANO_CONTA_BALANCETE",
    "VALOR_BALANCETE",
    "DIFERENCA",
    "RESULTADO_VALIDACAO",
    "DIAGNOSTICO",
    "MES_MATCH_ALTERNATIVO",
    "OBSERVACAO",
]


def _find_safe_start_row(values: List[List[str]], code_col: int, target_cols: List[int]) -> int:
    """Acha a primeira linha realmente livre para escrever, sem sobrescrever
    dados existentes caso haja um "buraco" seguido de linhas já preenchidas."""
    first_blank_row: Optional[int] = None
    for row_idx, row in enumerate(values[1:], start=2):
        blank = all(len(row) <= c or not str(row[c]).strip() for c in target_cols)
        if blank:
            first_blank_row = row_idx
            break

    if first_blank_row is None:
        return len(values) + 1

    for row_idx, row in enumerate(values[1:], start=2):
        if row_idx <= first_blank_row:
            continue
        if len(row) > code_col and str(row[code_col]).strip():
            return len(values) + 1

    return first_blank_row


class GoogleSheetsService:
    """Cliente Google Sheets resiliente com batch update e retry para cota 429."""

    def __init__(
        self,
        settings: Settings,
        origin_registry: Optional[Dict[str, OriginConfig]] = None,
    ):
        self.settings = settings
        credentials_path = Path(settings.google_credentials_path)
        if not credentials_path.is_file():
            raise FileNotFoundError(
                "Credenciais Google não encontradas em "
                f"'{credentials_path}'. Configure GOOGLE_CREDENTIALS_PATH no .env da raiz."
            )
        self._gc = gspread.service_account(filename=settings.google_credentials_path)
        self._spreadsheet_cache: Dict[str, Any] = {}
        self._sh = self._open_with_retry(settings.spreadsheet_url)
        self._spreadsheet_cache[settings.spreadsheet_url] = self._sh
        self._ws = self._sh.worksheet(settings.sheet_contaordem)
        self._headers_cache: Optional[List[str]] = None
        self._origin_registry = origin_registry or build_default_origin_registry(
            default_spreadsheet_url=settings.spreadsheet_url,
            app_verbo_cafe_url=getattr(settings, "app_verbo_cafe_spreadsheet_url", None),
        )

    @property
    def origin_registry(self) -> Dict[str, OriginConfig]:
        if not hasattr(self, "_origin_registry") or self._origin_registry is None:
            default_url = getattr(getattr(self, "settings", None), "spreadsheet_url", None)
            verbo_url = getattr(getattr(self, "settings", None), "app_verbo_cafe_spreadsheet_url", None)
            self._origin_registry = build_default_origin_registry(
                default_spreadsheet_url=default_url,
                app_verbo_cafe_url=verbo_url,
            )
        return self._origin_registry

    @property
    def spreadsheet_cache(self) -> Dict[str, Any]:
        if not hasattr(self, "_spreadsheet_cache") or self._spreadsheet_cache is None:
            self._spreadsheet_cache = {}
            if hasattr(self, "_sh") and self._sh is not None:
                default_url = getattr(getattr(self, "settings", None), "spreadsheet_url", None)
                if default_url:
                    self._spreadsheet_cache[default_url] = self._sh
        return self._spreadsheet_cache

    def register_spreadsheet(self, url: str, spreadsheet: Any) -> None:
        """Registra manualmente um Spreadsheet no cache (útil para testes ou injeção de dependência)."""
        self.spreadsheet_cache[url] = spreadsheet

    def register_origin(self, config: OriginConfig) -> None:
        """Registra ou sobrescreve uma configuração de origem."""
        self.origin_registry[norm_basic(config.processo)] = config

    def get_spreadsheet(self, spreadsheet_url: Optional[str] = None) -> Any:
        """Obtém uma instância de Spreadsheet (com cache e retry), usando a planilha principal se url for None."""
        if not spreadsheet_url:
            return self._sh
        default_url = getattr(getattr(self, "settings", None), "spreadsheet_url", None)
        if default_url and spreadsheet_url == default_url:
            return self._sh
        if spreadsheet_url in self.spreadsheet_cache:
            return self.spreadsheet_cache[spreadsheet_url]
        if hasattr(self, "_gc") and hasattr(self._gc, "open_by_url"):
            sh = self._open_with_retry(spreadsheet_url)
        elif hasattr(self, "_sh"):
            sh = self._sh
        else:
            raise RuntimeError(f"Não foi possível abrir o Spreadsheet '{spreadsheet_url}'")
        self.spreadsheet_cache[spreadsheet_url] = sh
        return sh

    def get_origin_config(self, processo: str) -> OriginConfig:
        key = norm_basic(processo)
        if key not in self.origin_registry:
            raise ValueError(f"PROCESSO sem planilha de origem configurada: '{processo}'")
        return self.origin_registry[key]

    def get_origin_worksheet(self, processo: str) -> Any:
        cfg = self.get_origin_config(processo)
        spreadsheet = self.get_spreadsheet(cfg.spreadsheet_url)
        return spreadsheet.worksheet(cfg.sheet_name)

    def _open_with_retry(self, url: str, max_retries: int = 5) -> gspread.Spreadsheet:
        for i in range(max_retries):
            try:
                return self._gc.open_by_url(url)
            except Exception as e:
                if "429" in str(e) and i < max_retries - 1:
                    logger.warning("Cota da Google Sheets excedida. Aguardando 20 segundos...")
                    time.sleep(20)
                else:
                    raise

    def get_headers(self) -> List[str]:
        if self._headers_cache is None:
            self._headers_cache = [str(h).strip() for h in self._ws.row_values(1)]
        return self._headers_cache

    @staticmethod
    def _col_letter(col_idx: int) -> str:
        res = ""
        while col_idx > 0:
            col_idx, remainder = divmod(col_idx - 1, 26)
            res = chr(65 + remainder) + res
        return res

    def get_all_rows(self, only_entrada_saida: bool = True) -> List[ContaOrdemRow]:
        """Retorna linhas da planilha CONTAORDEM.
        
        Por regra, lê apenas registros com TIPO igual a 'Entrada' ou 'Saída'.
        """
        for i in range(5):
            try:
                records = self._ws.get_all_records(
                    numericise_ignore=["all"],
                    value_render_option=ValueRenderOption.formatted,
                )
                out = []
                for idx, r in enumerate(records, start=2):
                    row = ContaOrdemRow.from_dict(row_number=idx, raw=r)
                    if only_entrada_saida and not is_entrada_ou_saida(row.tipo):
                        continue
                    out.append(row)
                return out
            except Exception as e:
                if "429" in str(e) and i < 4:
                    time.sleep(20)
                else:
                    raise

    def get_auditable_rows(self) -> List[ContaOrdemRow]:
        """Retorna linhas pendentes de auditoria (AUDITORIA vazia e TIPO Entrada ou Saída)."""
        all_rows = self.get_all_rows(only_entrada_saida=True)
        return [
            r for r in all_rows
            if not r.auditoria.strip() and is_entrada_ou_saida(r.tipo)
        ]

    def get_row(self, row_idx: int) -> Optional[ContaOrdemRow]:
        for i in range(5):
            try:
                headers = self.get_headers()
                vals = self._ws.row_values(row_idx, value_render_option=ValueRenderOption.formatted)
                if not vals:
                    return None
                while len(vals) < len(headers):
                    vals.append("")
                raw_dict = dict(zip(headers, vals))
                return ContaOrdemRow.from_dict(row_number=row_idx, raw=raw_dict)
            except Exception as e:
                if "429" in str(e) and i < 4:
                    time.sleep(15)
                else:
                    raise

    @staticmethod
    def _write_origin_cell(worksheet: Any, cell: str, value: str, max_retries: int = 5) -> None:
        """Atualiza uma célula na origem de forma compatível com gspread 5 e 6 com retry para cota 429."""
        for attempt in range(max_retries):
            try:
                try:
                    worksheet.update(cell, [[value]])
                except TypeError:
                    worksheet.update(range_name=cell, values=[[value]])
                return
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    time.sleep(15)
                else:
                    raise

    def _update_origin_doc(self, processo: str, id_interno: str, doc_id: str) -> None:
        """Atualiza DOC. SOMA na única linha da origem identificada por ID_INTERNO."""
        cfg = self.get_origin_config(processo)
        worksheet = self.get_origin_worksheet(processo)
        values = worksheet.get_all_values()
        if not values:
            raise ValueError(f"Planilha de origem '{cfg.sheet_name}' está vazia")

        header_map = {_normalize_header(h): i for i, h in enumerate(values[0])}
        id_col = header_map.get(_normalize_header(cfg.id_column_name))
        doc_col = header_map.get(_normalize_header(cfg.doc_column_name))
        if id_col is None or doc_col is None:
            raise ValueError(
                f"Origem '{cfg.sheet_name}' precisa das colunas {cfg.id_column_name} e {cfg.doc_column_name}"
            )

        matches = []
        for row_number, row in enumerate(values[1:], start=2):
            value = str(row[id_col]).strip() if id_col < len(row) else ""
            if value == str(id_interno).strip():
                matches.append((row_number, row))
        if len(matches) != 1:
            raise ValueError(
                f"ID_INTERNO '{id_interno}' encontrado {len(matches)} vez(es) na origem '{cfg.sheet_name}'"
            )

        row_number, source_row = matches[0]
        current_doc = str(source_row[doc_col]).strip() if doc_col < len(source_row) else ""
        if current_doc != str(doc_id).strip():
            cell = f"{self._col_letter(doc_col + 1)}{row_number}"
            self._write_origin_cell(worksheet, cell, str(doc_id).strip())
        logger.info(
            "DOC %s confirmado na origem %s, linha %s, ID_INTERNO %s.",
            doc_id, cfg.sheet_name, row_number, id_interno,
        )

    def update_caixas_bancos(self, saldos: Dict[str, str]) -> int:
        """Grava os saldos atuais de Caixas/Bancos na linha 2 da sheet GERENCIAR CAIXAS.
        Só escreve em colunas que já existem na sheet (mesma regra do processo legado)."""
        ws = self._sh.worksheet(self.settings.sheet_caixas)
        headers = ws.row_values(1)
        header_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}

        updates = []
        unmatched = []
        for label, valor in saldos.items():
            col_name = CAIXAS_LABEL_ALIASES.get(norm_basic(label), label)
            col = header_map.get(norm_basic(col_name))
            if col is None:
                unmatched.append(label)
                continue
            updates.append({"range": f"{self._col_letter(col)}2", "values": [[valor]]})

        ts_col = header_map.get(norm_basic("TIMESTAMP"))
        if ts_col:
            updates.append({
                "range": f"{self._col_letter(ts_col)}2",
                "values": [[time.strftime("%d/%m/%Y %H:%M:%S")]],
            })

        if unmatched:
            logger.warning(
                "Saldo(s) de Caixas/Bancos sem coluna correspondente em '%s': %s",
                self.settings.sheet_caixas, ", ".join(unmatched),
            )

        if not updates:
            return 0

        for attempt in range(5):
            try:
                ws.batch_update(updates, value_input_option=ValueInputOption.user_entered)
                break
            except Exception as e:
                if "429" in str(e) and attempt < 4:
                    time.sleep(15)
                else:
                    raise

        logger.info("Sheet '%s' atualizada com %d saldo(s).", self.settings.sheet_caixas, len(updates))
        return len(updates)

    def append_soma_rows(self, items: List[Any]) -> int:
        """Insere na sheet SOMA os lançamentos (SomaSearchResult) ainda não presentes
        (por CODIGO), sem nunca sobrescrever uma linha já preenchida."""
        ws = self._sh.worksheet(self.settings.sheet_soma)
        values = ws.get_all_values()
        if not values:
            raise ValueError(f"Planilha '{self.settings.sheet_soma}' está vazia")

        headers = values[0]
        header_map = {norm_basic(h): i for i, h in enumerate(headers)}
        missing = [name for name in SOMA_REPORT_COLUMNS if norm_basic(name) not in header_map]
        if missing:
            raise ValueError(
                f"Coluna(s) obrigatória(s) ausente(s) na sheet '{self.settings.sheet_soma}': "
                + ", ".join(missing)
            )

        code_col = header_map[norm_basic("CODIGO")]
        existing_codes = {
            normalize_document_value(row[code_col])
            for row in values[1:]
            if len(row) > code_col and str(row[code_col]).strip()
        }

        novos = [
            item for item in items
            if normalize_document_value(getattr(item, "codigo", "")) not in existing_codes
        ]
        if not novos:
            return 0

        target_cols = [header_map[norm_basic(name)] for name in SOMA_REPORT_COLUMNS]
        start_row = _find_safe_start_row(values, code_col, target_cols)

        last_row_needed = start_row + len(novos) - 1
        if last_row_needed > ws.row_count:
            ws.add_rows(last_row_needed - ws.row_count)

        updates = []
        for offset, item in enumerate(novos):
            row_idx = start_row + offset
            for col_name, attr in SOMA_REPORT_COLUMNS.items():
                col_idx = header_map[norm_basic(col_name)]
                value = getattr(item, attr, "")
                updates.append({"range": f"{self._col_letter(col_idx + 1)}{row_idx}", "values": [[value]]})

        for attempt in range(5):
            try:
                ws.batch_update(updates, value_input_option=ValueInputOption.user_entered)
                break
            except Exception as e:
                if "429" in str(e) and attempt < 4:
                    time.sleep(15)
                else:
                    raise

        logger.info(
            "Sheet '%s' atualizada: %d lançamento(s) novo(s) inseridos a partir da linha %d.",
            self.settings.sheet_soma, len(novos), start_row,
        )
        return len(novos)

    def get_repasse_existing_ids(self) -> Dict[str, str]:
        """Lê a T_REPASSE e devolve ID_VALIDACAO por chave funcional estável."""
        ws = self._sh.worksheet(self.settings.sheet_repasse)
        values = ws.get_all_values()
        if not values:
            return {}
        headers = [str(value).strip() for value in values[0]]
        header_map = {norm_basic(header): index for index, header in enumerate(headers)}
        required = [
            "ID_VALIDACAO",
            "INSTITUICAO",
            "ANO",
            "MES",
            "PLANO_CONTA_REPASSE",
        ]
        if any(norm_basic(name) not in header_map for name in required):
            return {}
        out: Dict[str, str] = {}
        for row in values[1:]:
            def cell(name: str) -> str:
                index = header_map[norm_basic(name)]
                return str(row[index]).strip() if index < len(row) else ""

            row_id = cell("ID_VALIDACAO")
            if not row_id:
                continue
            try:
                year = int(cell("ANO"))
            except ValueError:
                continue
            key = functional_key(
                cell("INSTITUICAO"),
                year,
                cell("MES"),
                cell("PLANO_CONTA_REPASSE"),
            )
            out[key] = row_id
        return out

    def upsert_repasse_records(self, records: List[RepasseValidationRecord]) -> int:
        """Cria/atualiza linhas na T_REPASSE sem duplicar a chave funcional."""
        if not records:
            return 0
        ws = self._sh.worksheet(self.settings.sheet_repasse)
        values = ws.get_all_values()
        if not values:
            ws.update("A1:Q1", [REPASSE_HEADERS])
            values = [REPASSE_HEADERS]

        headers = [str(value).strip() for value in values[0]]
        if all(not header for header in headers):
            ws.update("A1:Q1", [REPASSE_HEADERS])
            headers = REPASSE_HEADERS
            values = [REPASSE_HEADERS]

        missing = [header for header in REPASSE_HEADERS if header not in headers]
        if missing:
            raise ValueError(
                f"Coluna(s) obrigatória(s) ausente(s) na sheet '{self.settings.sheet_repasse}': "
                + ", ".join(missing)
            )
        header_map = {header: index for index, header in enumerate(headers)}

        existing_rows: Dict[str, int] = {}
        for row_number, row in enumerate(values[1:], start=2):
            try:
                year = int(str(row[header_map["ANO"]]).strip())
            except (ValueError, IndexError):
                continue
            def cell(name: str) -> str:
                index = header_map[name]
                return str(row[index]).strip() if index < len(row) else ""

            key = functional_key(
                cell("INSTITUICAO"),
                year,
                cell("MES"),
                cell("PLANO_CONTA_REPASSE"),
            )
            existing_rows[key] = row_number

        updates = []
        next_row = len(values) + 1
        for record in records:
            row_number = existing_rows.get(record.chave_funcional)
            if row_number is None:
                row_number = next_row
                next_row += 1
                existing_rows[record.chave_funcional] = row_number
            row_values = [
                record.id_validacao,
                record.data_execucao,
                record.instituicao,
                str(record.ano),
                record.mes,
                record.data_inicio,
                record.data_fim,
                record.plano_conta_repasse,
                format_decimal_pt(record.valor_repasse),
                record.status_repasse_soma,
                record.plano_conta_balancete,
                format_decimal_pt(record.valor_balancete),
                format_decimal_pt(record.diferenca),
                record.resultado_validacao,
                record.diagnostico,
                record.mes_match_alternativo,
                record.observacao,
            ]
            updates.append({
                "range": f"A{row_number}:Q{row_number}",
                "values": [row_values],
            })

        for attempt in range(5):
            try:
                ws.batch_update(updates, value_input_option=ValueInputOption.user_entered)
                break
            except Exception as e:
                if "429" in str(e) and attempt < 4:
                    time.sleep(15)
                else:
                    raise
        return len(updates)

    def mark_row_completed(
        self,
        row_idx: int,
        doc_id: str,
        dados_doc: str = "",
        elapsed_ms: int = 0,
        processo: str = "",
        id_interno: str = "",
    ) -> None:
        """Atualiza primeiro a origem e depois conclui a linha na CONTAORDEM."""
        doc_id = str(doc_id or "").strip()
        if not re.fullmatch(r"\d{7}", doc_id):
            raise ValueError("DOC. SOMA deve conter exatamente 7 dígitos numéricos")
        if not processo or not id_interno:
            try:
                row_data = self.get_row(row_idx)
                if row_data:
                    processo = processo or row_data.processo
                    id_interno = id_interno or row_data.id_interno
            except Exception:
                pass
        if not processo or not id_interno:
            raise ValueError("PROCESSO e ID_INTERNO são obrigatórios para atualizar a origem")
        headers = self.get_headers()
        header_map = {h.strip(): i + 1 for i, h in enumerate(headers)}
        missing_columns = [name for name in ("DOC. SOMA", "LINK") if name not in header_map]
        if missing_columns:
            raise ValueError(
                "Coluna(s) obrigatória(s) ausente(s) na CONTAORDEM: "
                + ", ".join(missing_columns)
            )

        self._update_origin_doc(processo, id_interno, doc_id)
        
        now_str = time.strftime("%d/%m/%Y %H:%M:%S")
        soma_url = (
            "https://verbodavida.info/IVV/"
            f"?mod=ivv&exec=entradas_saidas_dados&ID={doc_id}"
        )
        link_formula = f'=HYPERLINK("{soma_url}";"ACESSAR SOMA")'
        cells_to_update = [
            ("DOC. SOMA", doc_id),
            ("LINK", link_formula),
            ("STATUS", "VALIDADO"),
            ("AUDITORIA", "Confirmado"),
            ("IDUSER", self.settings.user_job_id),
            ("TIMESTAMP", now_str),
        ]
        if dados_doc:
            cells_to_update.append(("DADOS DOC", dados_doc))

        data_to_batch = []
        for col_name, val in cells_to_update:
            if col_name in header_map:
                c_idx = header_map[col_name]
                a1 = f"{self._col_letter(c_idx)}{row_idx}"
                data_to_batch.append({"range": a1, "values": [[val]]})

        if data_to_batch:
            for attempt in range(5):
                try:
                    self._ws.batch_update(
                        data_to_batch,
                        value_input_option=ValueInputOption.user_entered,
                    )
                    logger.info(f"Linha {row_idx} atualizada na sheet com DOC {doc_id}.")
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 4:
                        time.sleep(15)
                    else:
                        raise

    def sync_contaordem_row_to_origin(self, row_idx: int) -> bool:
        """Lê a linha da CONTAORDEM e sincroniza o DOC. SOMA de volta para a origem configurada."""
        row_data = self.get_row(row_idx)
        if not row_data:
            raise ValueError(f"Linha {row_idx} não encontrada na CONTAORDEM")
        doc_id = str(row_data.doc_soma or "").strip()
        if not re.fullmatch(r"\d{7}", doc_id):
            logger.warning(
                "Linha %s ignorada para sincronização na origem: DOC. SOMA inválido ('%s')",
                row_idx, doc_id,
            )
            return False
        if not row_data.processo or not row_data.id_interno:
            raise ValueError(
                f"Linha {row_idx} não possui PROCESSO ou ID_INTERNO para localizar a origem"
            )
        self._update_origin_doc(row_data.processo, row_data.id_interno, doc_id)
        return True

    def backfill_missing_links(self) -> int:
        """Preenche a coluna LINK com a formula de acesso ao SOMA para linhas com DOC numerico de 7 digitos."""
        rows = self._ws.get_all_values(value_render_option=ValueRenderOption.formula)
        if not rows:
            return 0
        header_map = {norm_basic(value): index for index, value in enumerate(rows[0])}
        doc_col = header_map.get(norm_basic("DOC. SOMA"))
        link_col = header_map.get(norm_basic("LINK"))
        if doc_col is None or link_col is None:
            return 0

        base_url = "https://verbodavida.info/IVV/?mod=ivv&exec=entradas_saidas_dados&ID="
        updates = []
        for row_number, row in enumerate(rows[1:], start=2):
            doc_id = str(row[doc_col]).strip() if doc_col < len(row) else ""
            if not re.fullmatch(r"\d{7}", doc_id):
                continue
            formula = f'=HYPERLINK("{base_url}{doc_id}";"ACESSAR SOMA")'
            current = str(row[link_col]).strip() if link_col < len(row) else ""
            if current != formula:
                updates.append({
                    "range": f"{self._col_letter(link_col + 1)}{row_number}",
                    "values": [[formula]],
                })

        if updates:
            for start in range(0, len(updates), 500):
                self._ws.batch_update(
                    updates[start : start + 500],
                    value_input_option=ValueInputOption.user_entered,
                )
            logger.info("Backfill automatico de links: %d links preenchidos.", len(updates))
        return len(updates)

    def claim_row(self, row_idx: int) -> Optional[str]:
        """Tenta reservar uma linha e confirma que este processo venceu a disputa."""
        headers = self.get_headers()
        header_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}
        status_col = header_map.get(norm_basic("STATUS"))
        if not status_col:
            raise RuntimeError("Coluna STATUS não encontrada na CONTAORDEM")
        token = f"EM PROCESSAMENTO:{int(time.time())}:{uuid.uuid4().hex}"
        cell = f"{self._col_letter(status_col)}{row_idx}"
        self._ws.update(cell, [[token]])
        current = self._ws.acell(cell).value or ""
        return token if current == token else None

    def mark_row_failed(self, row_idx: int, message: str) -> None:
        """Liberta a reserva e registra uma falha sem inventar DOC. SOMA."""
        headers = self.get_headers()
        header_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}
        updates = []
        for name, value in (
            ("STATUS", "EM ERRO"),
            ("AUDITORIA", str(message)[:450]),
            ("DADOS DOC", str(message)[:450]),
        ):
            col = header_map.get(norm_basic(name))
            if col:
                updates.append({"range": f"{self._col_letter(col)}{row_idx}", "values": [[value]]})
        if updates:
            self._ws.batch_update(updates)

    def mark_row_validation_error(self, row_idx: int, message: str) -> None:
        """Marca uma linha inválida para análise humana, sem acessar o SOMA."""
        headers = self.get_headers()
        header_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}
        updates = []
        for name, value in (
            ("DOC. SOMA", "Analisar"),
            ("STATUS", "ERRO"),
            ("AUDITORIA", str(message)[:450]),
            ("DADOS DOC", str(message)[:450]),
        ):
            col = header_map.get(norm_basic(name))
            if col:
                updates.append({"range": f"{self._col_letter(col)}{row_idx}", "values": [[value]]})
        if updates:
            self._ws.batch_update(updates)

    def mark_row_duplicate(self, row_idx: int, count: int) -> None:
        """Bloqueia criação quando a pesquisa preventiva encontra vários registros."""
        headers = self.get_headers()
        header_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}
        updates = []
        for name, value in (
            ("DOC. SOMA", "Analisar"),
            ("STATUS", "Duplicidade"),
            ("AUDITORIA", f"Duplicidade: pesquisa encontrou {count} registros no SOMA"),
            ("DADOS DOC", f"Pesquisa preventiva encontrou {count} registros no SOMA"),
        ):
            col = header_map.get(norm_basic(name))
            if col:
                updates.append({"range": f"{self._col_letter(col)}{row_idx}", "values": [[value]]})
        if updates:
            self._ws.batch_update(updates)

    def mark_row_audit(
        self,
        row_idx: int,
        auditoria: str,
        new_doc: Optional[str] = None,
        new_desc: Optional[str] = None,
        dados_doc: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        """Atualiza os campos de auditoria de uma linha individualmente."""
        headers = self.get_headers()
        header_map = {h.strip(): i + 1 for i, h in enumerate(headers)}

        cells_to_update = [("AUDITORIA", auditoria)]
        if new_doc is not None:
            cells_to_update.append(("DOC. SOMA", new_doc))
        if new_desc is not None:
            cells_to_update.append(("DESCRIÇÃO SOMA", new_desc))
        if dados_doc is not None:
            cells_to_update.append(("DADOS DOC", dados_doc))
        if status is not None:
            cells_to_update.append(("STATUS", status))

        data_to_batch = []
        for col_name, val in cells_to_update:
            if col_name in header_map:
                c_idx = header_map[col_name]
                a1 = f"{self._col_letter(c_idx)}{row_idx}"
                data_to_batch.append({"range": a1, "values": [[val]]})

        if data_to_batch:
            for attempt in range(5):
                try:
                    self._ws.batch_update(data_to_batch)
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 4:
                        time.sleep(15)
                    else:
                        raise

    def batch_update_audit_records(self, updates_list: List[Dict[str, Any]]) -> None:
        """Aplica múltiplas auditorias de uma só vez para máxima velocidade."""
        if not updates_list:
            return
        headers = self.get_headers()
        norm_map = {norm_basic(h): i + 1 for i, h in enumerate(headers)}
        data_to_batch = []
        for upd in updates_list:
            row_idx = upd["row_idx"]
            cells = []
            if upd.get("auditoria") is not None:
                cells.append(("AUDITORIA", upd["auditoria"]))
            if upd.get("new_doc") is not None:
                cells.append(("DOC. SOMA", upd["new_doc"]))
            if upd.get("new_desc") is not None:
                cells.append(("DESCRIÇÃO SOMA", upd["new_desc"]))
            if upd.get("new_tipo") is not None:
                cells.append(("TIPO", upd["new_tipo"]))
            if upd.get("new_data") is not None:
                cells.append(("DATA MOV.", upd["new_data"]))
            if upd.get("dados_doc") is not None:
                cells.append(("DADOS DOC", upd["dados_doc"]))
            if upd.get("status") is not None:
                cells.append(("STATUS", upd["status"]))
            if upd.get("new_caixa") is not None:
                cells.append(("CAIXA", upd["new_caixa"]))
            if upd.get("new_forma_pagamento") is not None:
                cells.append(("FORMA DE PAGAMENTO", upd["new_forma_pagamento"]))

            for col_name, val in cells:
                col_norm = norm_basic(col_name)
                if col_norm in norm_map:
                    c_idx = norm_map[col_norm]
                    a1 = f"{self._col_letter(c_idx)}{row_idx}"
                    data_to_batch.append({"range": a1, "values": [[val]]})

        if data_to_batch:
            chunk_size = 500
            for i in range(0, len(data_to_batch), chunk_size):
                chunk = data_to_batch[i : i + chunk_size]
                for attempt in range(5):
                    try:
                        self._ws.batch_update(chunk)
                        break
                    except Exception as e:
                        if "429" in str(e) and attempt < 4:
                            time.sleep(20)
                        else:
                            raise

    def harmonize_sequentials_and_duplicates(self, update_sheet: bool = True) -> Dict[str, Any]:
        """Pré-validação e harmonização de sequenciais Nxxx e DOC. SOMA duplicados na planilha CONTAORDEM:
        1. Lê todas as linhas da planilha.
        2. Agrupa lançamentos por lote de data (DATA MOV.), tipo (Entrada/Saída) e descrição base (sem data e sem Nxxx).
        3. Para lotes com sequenciais duplicados, fora de ordem ou DOCs SOMA duplicados:
           - Re-sequencia apenas linhas ainda sem DOC numérico. Linhas conciliadas preservam
             a descrição confirmada no SOMA.
           - Limpa o DOC. SOMA, AUDITORIA e STATUS nas linhas subsequentes onde o DOC numérico foi duplicado.
        4. Se update_sheet=True, grava as alterações na planilha Google Sheets via batch_update.
        5. Retorna estatísticas e lista de linhas ajustadas.
        """
        headers = self.get_headers()
        norm_map = {norm_basic(h): i for i, h in enumerate(headers)}

        dt_idx = norm_map.get(norm_basic("DATA MOV."), 0)
        tipo_idx = norm_map.get(norm_basic("TIPO"), 6)
        doc_idx = norm_map.get(norm_basic("DOC. SOMA"), 4)
        desc_idx = norm_map.get(norm_basic("DESCRIÇÃO"), 2)
        desc_soma_idx = norm_map.get(norm_basic("DESCRIÇÃO SOMA"), 9)
        val_idx = norm_map.get(norm_basic("IMPORTÂNCIA"), 3)

        all_vals = []
        for attempt in range(5):
            try:
                all_vals = self._ws.get_all_values()
                break
            except Exception as e:
                if "429" in str(e) and attempt < 4:
                    logger.warning("Cota excedida ao ler planilha. Aguardando 20s...")
                    time.sleep(20)
                else:
                    raise

        data_rows = all_vals[1:]
        groups = defaultdict(list)
        for r_idx, r in enumerate(data_rows, start=2):
            while len(r) < len(headers):
                r.append("")
            t = r[tipo_idx].strip()
            dt = r[dt_idx].strip()
            if not dt or not is_entrada_ou_saida(t):
                continue
            fdesc = r[desc_soma_idx].strip() or r[desc_idx].strip()
            base = strip_date_prefix(strip_suffix_n(fdesc)).strip()
            groups[(dt, norm_basic(t), base.upper())].append((r_idx, r, base))

        updates_list = []
        batches_affected = set()
        total_desc_adjusted = 0
        total_docs_cleared = 0
        adjustments_summary = []

        for (dt, tipo, base_upper), items in groups.items():
            if len(items) <= 1:
                continue

            seqs = [r[desc_soma_idx].strip() for _, r, _ in items]
            docs = [r[doc_idx].strip() for _, r, _ in items]
            has_dupe_seq = len(set(seqs)) < len(seqs)
            has_dupe_doc = len([d for d in docs if d.isdigit()]) > len(set([d for d in docs if d.isdigit()]))
            has_missing_seq = any(not re.search(r"\bN\d{3}\b", s) for s in seqs)

            if has_dupe_seq or has_dupe_doc or has_missing_seq:
                batches_affected.add(dt)
                base_clean = items[0][2]
                seen_docs = set()

                for i, (r_idx, r, _) in enumerate(items, start=1):
                    expected_desc = f"{base_clean} N{i:03d}"
                    cur_desc = r[desc_soma_idx].strip()
                    cur_doc = r[doc_idx].strip()

                    row_upd = {"row_idx": r_idx}
                    changed = False

                    # Um DOC numérico já identifica inequivocamente o lançamento. A descrição
                    # oficial do SOMA não pode ser sobrescrita pela ordem física da sheet.
                    if not cur_doc.isdigit() and cur_desc != expected_desc:
                        row_upd["new_desc"] = expected_desc
                        total_desc_adjusted += 1
                        changed = True

                    if cur_doc.isdigit():
                        if cur_doc in seen_docs:
                            # DOC duplicado neste grupo! Limpa DOC. SOMA, STATUS, AUDITORIA e DADOS DOC
                            row_upd["new_doc"] = ""
                            row_upd["status"] = ""
                            row_upd["auditoria"] = ""
                            row_upd["dados_doc"] = ""
                            total_docs_cleared += 1
                            changed = True
                            adjustments_summary.append({
                                "row": r_idx,
                                "data": dt,
                                "valor": r[val_idx],
                                "desc_antiga": cur_desc,
                                "desc_nova": expected_desc,
                                "doc_limpo": cur_doc,
                            })
                        else:
                            seen_docs.add(cur_doc)

                    if changed:
                        updates_list.append(row_upd)

        logger.info(
            f"Harmonização pré-validação: {len(batches_affected)} lotes de data afetados, "
            f"{total_desc_adjusted} descrições ajustadas, {total_docs_cleared} DOCs duplicados limpos."
        )

        if update_sheet and updates_list:
            self.batch_update_audit_records(updates_list)
            logger.info("Planilha CONTAORDEM atualizada com sucesso após harmonização.")

        return {
            "batches_affected": len(batches_affected),
            "total_desc_adjusted": total_desc_adjusted,
            "total_docs_cleared": total_docs_cleared,
            "updates_count": len(updates_list),
            "adjustments_summary": adjustments_summary,
        }


