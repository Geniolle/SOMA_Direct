from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.audit_service import AuditService
from services.reconciliation_resolver import ReconciliationResolver
from services.sheets_service import GoogleSheetsService
from domain.models import clean_amount_for_comparison, norm_basic, normalize_date_str

logger = logging.getLogger("soma_direct.reconciliation_orchestrator")

SOURCE_SHEETS = (
    "T_EXTRATO",
    "DÍZIMOS/OFERTAS",
    "SAÍDAS",
    "Financeiro",
    "VC_VENDAS",
)
EXTERNAL_SOURCE_SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "11sUHhTzKaV21uX_FpBOEnJxNpFjUn6EHEiU79Pe3jXU/edit"
)
EXTERNAL_SOURCE_SHEETS = {"Financeiro", "VC_VENDAS"}


@dataclass(frozen=True)
class OriginIdValidation:
    source_sheet: str
    source_row: Optional[int]
    id_interno: str
    status: str
    direction: str = "ORIGEM_PARA_CONTAORDEM"
    contaordem_rows: tuple[int, ...] = ()


@dataclass(frozen=True)
class OriginSheetValidation:
    source_sheet: str
    rows_checked: int
    found: int
    missing: int
    blank: int
    duplicate_in_source: int
    duplicate_in_contaordem: int
    items: tuple[OriginIdValidation, ...]
    contaordem_rows_checked: int = 0
    missing_in_source: int = 0
    error: Optional[str] = None


@dataclass(frozen=True)
class OriginValidationReport:
    sources: tuple[OriginSheetValidation, ...]
    contaordem_ids: int
    contaordem_duplicate_ids: int
    blocked_by_duplicates: bool = False

    def summary(self) -> Dict[str, int]:
        return {
            "sources": len(self.sources),
            "rows_checked": sum(source.rows_checked for source in self.sources),
            "found": sum(source.found for source in self.sources),
            "missing": sum(source.missing for source in self.sources),
            "blank": sum(source.blank for source in self.sources),
            "duplicate_in_source": sum(source.duplicate_in_source for source in self.sources),
            "duplicate_in_contaordem": sum(source.duplicate_in_contaordem for source in self.sources),
            "contaordem_rows_checked": sum(source.contaordem_rows_checked for source in self.sources),
            "missing_in_source": sum(source.missing_in_source for source in self.sources),
            "source_errors": sum(1 for source in self.sources if source.error),
            "blocked_by_duplicates": int(self.blocked_by_duplicates),
        }


@dataclass(frozen=True)
class TransferIdMatch:
    contaordem_row: int
    extrato_row: int
    id_interno: str
    data_mov: str
    valor: str


@dataclass(frozen=True)
class TransferIdPlan:
    matches: tuple[TransferIdMatch, ...]
    unmatched_rows: tuple[int, ...]
    ambiguous_rows: tuple[int, ...]


@dataclass(frozen=True)
class ReconciliationPlan:
    sheet_updates: List[Dict[str, Any]]
    origin_updates: List[Dict[str, Any]]
    soma_updates: List[Dict[str, Any]]

    @property
    def total_updates(self) -> int:
        return len(self.sheet_updates) + len(self.origin_updates) + len(self.soma_updates)

    def summary(self) -> Dict[str, int]:
        return {
            "contaordem": len(self.sheet_updates),
            "origem": len(self.origin_updates),
            "soma": len(self.soma_updates),
            "total": self.total_updates,
        }


class ReconciliationOrchestrator:
    """Coordena o planeamento e a aplicação da conciliação ponta a ponta."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        sheets: Optional[GoogleSheetsService] = None,
    ):
        self.settings = settings or Settings.from_env()
        self.http = ResilientSession(timeout=self.settings.timeout_seconds)
        self.auth = SomaAuthenticator(self.settings, self.http)
        self.sheets = sheets or GoogleSheetsService(self.settings)
        self.audit = AuditService(self.settings, self.http, self.sheets)
        self._resolver: Optional[ReconciliationResolver] = None
        self._external_source_spreadsheet: Optional[Any] = None
        self._initialized = False

    def _get_source_worksheet(self, source_name: str) -> Any:
        if source_name not in EXTERNAL_SOURCE_SHEETS:
            return self.sheets._sh.worksheet(source_name)
        if self._external_source_spreadsheet is None:
            self._external_source_spreadsheet = self.sheets._gc.open_by_url(
                EXTERNAL_SOURCE_SPREADSHEET_URL
            )
        return self._external_source_spreadsheet.worksheet(source_name)

    @staticmethod
    def _find_id_column(headers: List[str]) -> int:
        expected = norm_basic("ID_INTERNO").replace("_", " ")
        for index, header in enumerate(headers):
            normalized = norm_basic(header).replace("_", " ")
            if normalized == expected:
                return index
        raise ValueError("Coluna ID_INTERNO não encontrada")

    @staticmethod
    def _find_column(headers: List[str], column_name: str) -> int:
        expected = norm_basic(column_name).replace("_", " ")
        for index, header in enumerate(headers):
            normalized = norm_basic(header).replace("_", " ")
            if normalized == expected:
                return index
        raise ValueError(f"Coluna {column_name} não encontrada")

    def _read_ids_by_row(self, worksheet: Any) -> Dict[str, List[int]]:
        values = worksheet.get_all_values()
        if not values:
            return {}

        id_column = self._find_id_column(values[0])
        ids_by_row: Dict[str, List[int]] = defaultdict(list)
        for row_number, row in enumerate(values[1:], start=2):
            id_interno = str(row[id_column]).strip() if id_column < len(row) else ""
            ids_by_row[id_interno].append(row_number)
        return dict(ids_by_row)

    def _read_contaordem(self) -> tuple[Dict[str, List[int]], Dict[str, List[tuple[int, str]]]]:
        values = self.sheets._ws.get_all_values()
        if not values:
            return {}, {}

        id_column = self._find_id_column(values[0])
        process_column = self._find_column(values[0], "PROCESSO")
        ids_by_row: Dict[str, List[int]] = defaultdict(list)
        rows_by_process: Dict[str, List[tuple[int, str]]] = defaultdict(list)

        for row_number, row in enumerate(values[1:], start=2):
            id_interno = str(row[id_column]).strip() if id_column < len(row) else ""
            process = str(row[process_column]).strip() if process_column < len(row) else ""
            if id_interno:
                ids_by_row[id_interno].append(row_number)
                rows_by_process[norm_basic(process)].append((row_number, id_interno))
        return dict(ids_by_row), dict(rows_by_process)

    @staticmethod
    def _row_value(row: List[str], indices: Dict[str, int], field: str) -> str:
        index = indices.get(norm_basic(field))
        return str(row[index]).strip() if index is not None and index < len(row) else ""

    @staticmethod
    def _absolute_amount(value: str) -> str:
        normalized = clean_amount_for_comparison(value)
        try:
            return str(abs(Decimal(normalized.replace(",", "."))))
        except Exception:
            return normalized

    def build_transfer_id_plan(self) -> TransferIdPlan:
        """Planeia IDs para transferências sem ID usando tipo, data e valor da T_EXTRATO."""
        conta_values = self.sheets._ws.get_all_values()
        extrato_values = self.sheets._sh.worksheet("T_EXTRATO").get_all_values()
        conta_indices = {norm_basic(header): index for index, header in enumerate(conta_values[0])}
        extrato_indices = {norm_basic(header): index for index, header in enumerate(extrato_values[0])}
        self._find_id_column(conta_values[0])
        self._find_id_column(extrato_values[0])

        used_ids = {
            self._row_value(row, conta_indices, "ID_INTERNO")
            for row in conta_values[1:]
            if self._row_value(row, conta_indices, "ID_INTERNO")
        }
        extrato_by_key: Dict[tuple[str, str], List[tuple[int, str]]] = defaultdict(list)
        for row_number, row in enumerate(extrato_values[1:], start=2):
            if norm_basic(self._row_value(row, extrato_indices, "TIPO")) != "transferencia":
                continue
            id_interno = self._row_value(row, extrato_indices, "ID_INTERNO")
            if not id_interno or id_interno in used_ids:
                continue
            key = (
                normalize_date_str(self._row_value(row, extrato_indices, "DATA MOV.")),
                self._absolute_amount(self._row_value(row, extrato_indices, "IMPORTÂNCIA")),
            )
            extrato_by_key[key].append((row_number, id_interno))

        matches: List[TransferIdMatch] = []
        unmatched_rows: List[int] = []
        ambiguous_rows: List[int] = []
        allocated_ids: set[str] = set()
        for row_number, row in enumerate(conta_values[1:], start=2):
            if norm_basic(self._row_value(row, conta_indices, "TIPO")) != "transferencia":
                continue
            if self._row_value(row, conta_indices, "ID_INTERNO"):
                continue
            data_mov = normalize_date_str(self._row_value(row, conta_indices, "DATA MOV."))
            valor = self._row_value(row, conta_indices, "IMPORTÂNCIA")
            candidates = [
                candidate for candidate in extrato_by_key.get((data_mov, self._absolute_amount(valor)), [])
                if candidate[1] not in allocated_ids
            ]
            if len(candidates) == 1:
                extrato_row, id_interno = candidates[0]
                allocated_ids.add(id_interno)
                matches.append(TransferIdMatch(row_number, extrato_row, id_interno, data_mov, valor))
            elif candidates:
                ambiguous_rows.append(row_number)
            else:
                unmatched_rows.append(row_number)
        return TransferIdPlan(tuple(matches), tuple(unmatched_rows), tuple(ambiguous_rows))

    def apply_transfer_id_plan(self, plan: TransferIdPlan) -> int:
        """Aplica apenas correspondências ainda válidas e com ID vazio na CONTAORDEM."""
        headers = self.sheets.get_headers()
        id_column = self._find_id_column(headers) + 1
        updates = []
        applied_ids: set[str] = set()
        for match in plan.matches:
            row = self.sheets._ws.row_values(match.contaordem_row)
            indices = {norm_basic(header): index for index, header in enumerate(headers)}
            if self._row_value(row, indices, "ID_INTERNO"):
                continue
            if norm_basic(self._row_value(row, indices, "TIPO")) != "transferencia":
                continue
            if normalize_date_str(self._row_value(row, indices, "DATA MOV.")) != match.data_mov:
                continue
            if self._absolute_amount(self._row_value(row, indices, "IMPORTÂNCIA")) != self._absolute_amount(match.valor):
                continue
            if match.id_interno in applied_ids:
                continue
            applied_ids.add(match.id_interno)
            updates.append({
                "range": f"{self.sheets._col_letter(id_column)}{match.contaordem_row}",
                "values": [[match.id_interno]],
            })
        if updates:
            self.sheets._ws.batch_update(updates)
        return len(updates)

    def validate_origin_ids(
        self,
        source_sheets: tuple[str, ...] = SOURCE_SHEETS,
    ) -> OriginValidationReport:
        """Valida duplicidades antes de cruzar ID_INTERNO entre origens e CONTAORDEM."""
        contaordem_ids, contaordem_by_process = self._read_contaordem()
        contaordem_duplicate_ids = sum(
            1 for rows in contaordem_ids.values() if len(rows) > 1
        )
        loaded_sources: Dict[str, Dict[str, List[int]]] = {}
        source_errors: Dict[str, str] = {}

        for source_name in source_sheets:
            try:
                worksheet = self._get_source_worksheet(source_name)
                loaded_sources[source_name] = self._read_ids_by_row(worksheet)
            except Exception as exc:
                logger.error("Falha ao carregar a origem %s: %s", source_name, exc)
                source_errors[source_name] = str(exc)

        source_has_duplicates = any(
            len(rows) > 1
            for source_ids in loaded_sources.values()
            for id_interno, rows in source_ids.items()
            if id_interno
        )
        blocked_by_duplicates = contaordem_duplicate_ids > 0 or source_has_duplicates
        source_reports: List[OriginSheetValidation] = []

        for source_name in source_sheets:
            if source_name in source_errors:
                source_reports.append(
                    OriginSheetValidation(
                        source_sheet=source_name,
                        rows_checked=0,
                        found=0,
                        missing=0,
                        blank=0,
                        duplicate_in_source=0,
                        duplicate_in_contaordem=0,
                        items=(),
                        error=source_errors[source_name],
                    )
                )
                continue

            source_ids = loaded_sources[source_name]
            items: List[OriginIdValidation] = []
            contaordem_source_rows = contaordem_by_process.get(norm_basic(source_name), [])

            if blocked_by_duplicates:
                for id_interno, source_rows in source_ids.items():
                    if id_interno and len(source_rows) > 1:
                        for source_row in source_rows:
                            items.append(
                                OriginIdValidation(
                                    source_sheet=source_name,
                                    source_row=source_row,
                                    id_interno=id_interno,
                                    status="DUPLICADO_NA_ORIGEM",
                                )
                            )

                for contaordem_row, id_interno in contaordem_source_rows:
                    if len(contaordem_ids[id_interno]) > 1:
                        items.append(
                            OriginIdValidation(
                                source_sheet=source_name,
                                source_row=None,
                                id_interno=id_interno,
                                status="DUPLICADO_NA_CONTAORDEM",
                                direction="CONTAORDEM_PARA_ORIGEM",
                                contaordem_rows=(contaordem_row,),
                            )
                        )

                source_reports.append(
                    OriginSheetValidation(
                        source_sheet=source_name,
                        rows_checked=sum(len(rows) for rows in source_ids.values()),
                        found=0,
                        missing=0,
                        blank=0,
                        duplicate_in_source=sum(item.status == "DUPLICADO_NA_ORIGEM" for item in items),
                        duplicate_in_contaordem=sum(item.status == "DUPLICADO_NA_CONTAORDEM" for item in items),
                        items=tuple(items),
                        contaordem_rows_checked=len(contaordem_source_rows),
                    )
                )
                continue

            try:
                for id_interno, source_rows in source_ids.items():
                    for source_row in source_rows:
                        contaordem_rows = tuple(contaordem_ids.get(id_interno, []))
                        if not id_interno:
                            status = "ID_VAZIO"
                        elif len(source_rows) > 1:
                            status = "DUPLICADO_NA_ORIGEM"
                        elif not contaordem_rows:
                            status = "NAO_ENCONTRADO"
                        elif len(contaordem_rows) > 1:
                            status = "DUPLICADO_NA_CONTAORDEM"
                        else:
                            status = "ENCONTRADO"
                        items.append(
                            OriginIdValidation(
                                source_sheet=source_name,
                                source_row=source_row,
                                id_interno=id_interno,
                                status=status,
                                contaordem_rows=contaordem_rows,
                            )
                        )

                source_non_blank_ids = {id_interno for id_interno in source_ids if id_interno}
                for contaordem_row, id_interno in contaordem_source_rows:
                    exists_in_source = id_interno in source_non_blank_ids
                    items.append(
                        OriginIdValidation(
                            source_sheet=source_name,
                            source_row=None,
                            id_interno=id_interno,
                            status="ENCONTRADO_NA_ORIGEM" if exists_in_source else "NAO_ENCONTRADO_NA_ORIGEM",
                            direction="CONTAORDEM_PARA_ORIGEM",
                            contaordem_rows=(contaordem_row,),
                        )
                    )

                source_reports.append(
                    OriginSheetValidation(
                        source_sheet=source_name,
                        rows_checked=sum(len(rows) for rows in source_ids.values()),
                        found=sum(item.status == "ENCONTRADO" for item in items),
                        missing=sum(item.status == "NAO_ENCONTRADO" for item in items),
                        blank=sum(item.status == "ID_VAZIO" for item in items),
                        duplicate_in_source=sum(item.status == "DUPLICADO_NA_ORIGEM" for item in items),
                        duplicate_in_contaordem=sum(item.status == "DUPLICADO_NA_CONTAORDEM" for item in items),
                        items=tuple(items),
                        contaordem_rows_checked=len(contaordem_source_rows),
                        missing_in_source=sum(item.status == "NAO_ENCONTRADO_NA_ORIGEM" for item in items),
                    )
                )
            except Exception as exc:
                logger.error("Falha ao validar a origem %s: %s", source_name, exc)
                source_reports.append(
                    OriginSheetValidation(
                        source_sheet=source_name,
                        rows_checked=0,
                        found=0,
                        missing=0,
                        blank=0,
                        duplicate_in_source=0,
                        duplicate_in_contaordem=0,
                        items=(),
                        contaordem_rows_checked=0,
                        missing_in_source=0,
                        error=str(exc),
                    )
                )

        report = OriginValidationReport(
            sources=tuple(source_reports),
            contaordem_ids=len(contaordem_ids),
            contaordem_duplicate_ids=contaordem_duplicate_ids,
            blocked_by_duplicates=blocked_by_duplicates,
        )
        logger.info("Validação de IDs das origens concluída: %s", report.summary())
        return report

    def initialize(self) -> None:
        if self._initialized:
            return
        if not self.auth.login():
            raise RuntimeError("Não foi possível autenticar no SOMA.")
        self._resolver = ReconciliationResolver(
            self.settings,
            self.http,
            self.sheets,
            self.audit,
        )
        self._initialized = True

    def build_plan(self) -> ReconciliationPlan:
        self.initialize()
        if self._resolver is None:
            raise RuntimeError("Resolvedor de conciliação não inicializado.")

        sheet_updates, origin_updates, soma_updates = self._resolver.build_resolutions()
        plan = ReconciliationPlan(
            sheet_updates=sheet_updates,
            origin_updates=origin_updates,
            soma_updates=soma_updates,
        )
        logger.info("Plano de conciliação calculado: %s", plan.summary())
        return plan

    def apply_plan(self, plan: ReconciliationPlan) -> Dict[str, int]:
        self.initialize()
        if self._resolver is None:
            raise RuntimeError("Resolvedor de conciliação não inicializado.")

        self._resolver.apply_resolutions(
            plan.sheet_updates,
            plan.origin_updates,
            plan.soma_updates,
        )
        summary = plan.summary()
        logger.info("Plano de conciliação aplicado: %s", summary)
        return summary

    def run(self, dry_run: bool = True) -> ReconciliationPlan:
        plan = self.build_plan()
        if dry_run:
            logger.info("Conciliação em modo de simulação; nenhuma alteração aplicada.")
            return plan

        self.apply_plan(plan)
        return plan
