from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from domain.models import (
    ContaOrdemRow,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
)

REQUIRED_HEADERS = (
    "DATA MOV.",
    "IMPORTÂNCIA",
    "DOC. SOMA",
    "TIPO",
    "DESCRIÇÃO SOMA",
    "FORMA DE PAGAMENTO",
    "CAIXA",
    "SOMA",
)
RETRYABLE_DATE_ERROR = "ERRO: DATA MOV. divergente"


@dataclass
class BatchDetail:
    row: int
    document: str
    result: str
    operation: str = "NENHUMA"
    reason: str = ""
    payments: list[str] = field(default_factory=list)
    pending_baixas: list[str] = field(default_factory=list)


@dataclass
class BatchReport:
    period: str = ""
    period_rows: int = 0
    invalid_dates: int = 0
    total_rows: int = 0
    empty_at_start: int = 0
    eligible: int = 0
    ignored_filled: int = 0
    ignored_non_eligible_type: int = 0
    documents_consulted: int = 0
    validated_without_correction: int = 0
    payments_created: int = 0
    baixas_executed: int = 0
    automatic_baixas: int = 0
    dates_corrected: int = 0
    date_error_retries: int = 0
    definitive_errors: int = 0
    temporary_failures: int = 0
    uncertain_results: int = 0
    validated_written: int = 0
    errors_written: int = 0
    readbacks_confirmed: int = 0
    details: list[BatchDetail] = field(default_factory=list)

    @property
    def pending(self) -> int:
        return sum(item.result == "PENDENTE" for item in self.details)

    @property
    def ignored_total(self) -> int:
        return self.ignored_filled + self.ignored_non_eligible_type

    def to_dict(self) -> dict:
        data = asdict(self)
        data["pending"] = self.pending
        data["ignored_total"] = self.ignored_total
        return data


class SomaAuditBatchOrchestrator:
    """Audita SOMA vazio; dry-run nunca chama métodos financeiros nem Sheets update."""

    def __init__(
        self,
        sheets,
        audit,
        authenticate: Callable[[], bool],
        checkpoint_path: Path | None = None,
        flush_interval_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.sheets = sheets
        self.audit = audit
        self.authenticate = authenticate
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.flush_interval_seconds = flush_interval_seconds
        self.clock = clock

    @staticmethod
    def _header_map(headers: list[str]) -> dict[str, int]:
        normalized = [norm_basic(item) for item in headers if str(item).strip()]
        duplicates = sorted(key for key, count in Counter(normalized).items() if count > 1)
        if duplicates:
            raise RuntimeError("Cabeçalho duplicado ou ambíguo: " + ", ".join(duplicates))
        result = {}
        for name in REQUIRED_HEADERS:
            matches = [i for i, header in enumerate(headers) if norm_basic(header) == norm_basic(name)]
            if len(matches) != 1:
                raise RuntimeError(f"Cabeçalho obrigatório ausente ou ambíguo: {name}")
            result[name] = matches[0]
        return result

    @staticmethod
    def _cell(cells: list[str], index: int) -> str:
        return str(cells[index]).strip() if index < len(cells) else ""

    @staticmethod
    def _eligible_soma(value: str) -> bool:
        return value.strip() in ("", RETRYABLE_DATE_ERROR)

    @staticmethod
    def _normalize_period(periodo: str) -> str:
        try:
            month, year = (int(part) for part in str(periodo).strip().split("/"))
            parsed = date(year, month, 1)
        except (TypeError, ValueError) as exc:
            raise ValueError("Período inválido; utilize MM/AAAA") from exc
        return parsed.strftime("%m/%Y")

    @staticmethod
    def _row_period(value: str) -> str | None:
        try:
            day, month, year = (int(part) for part in normalize_date_str(value).split("/"))
            parsed = date(year, month, day)
        except (TypeError, ValueError):
            return None
        return parsed.strftime("%m/%Y")

    def available_periods(self) -> dict[str, int]:
        values = self.sheets._ws.get_all_values()
        if not values:
            raise RuntimeError("CONTAORDEM vazia")
        headers = [str(item).strip() for item in values[0]]
        header_map = self._header_map(headers)
        periods: Counter[str] = Counter()
        for number, cells in enumerate(values[1:], start=2):
            raw = {name: self._cell(cells, index) for name, index in header_map.items()}
            row = ContaOrdemRow.from_dict(number, raw)
            if not self._eligible_soma(self._cell(cells, header_map["SOMA"])) or row.tipo.value not in ("Entrada", "Saída"):
                continue
            period = self._row_period(row.data_mov)
            if period:
                periods[period] += 1
        return dict(sorted(periods.items(), key=lambda item: tuple(reversed(item[0].split("/")))))

    @staticmethod
    def _financial_error(errors: list[str]) -> str:
        joined = " | ".join(errors)
        for marker, label in (
            ("DOC", "DOC. SOMA divergente"),
            ("TIPO", "TIPO divergente"),
            ("DATA", "DATA MOV. divergente"),
            ("DESCRIÇÃO", "DESCRIÇÃO SOMA divergente"),
            ("VALOR", "IMPORTÂNCIA divergente"),
        ):
            if marker in joined.upper():
                return f"ERRO: {label}"
        return "ERRO: divergência financeira"

    @staticmethod
    def _only_date_error(errors: list[str]) -> bool:
        return len(errors) == 1 and errors[0].upper().startswith("DATA (COL 7):")

    def _classify_row(self, row: ContaOrdemRow) -> BatchDetail:
        document = normalize_document_value(row.doc_soma)
        result = self.audit.search_by_codigo(document)
        if result is None or normalize_document_value(result.codigo) != document:
            return BatchDetail(row.row_number, document, "ERRO: documento não encontrado", "BLOQUEADO")
        errors = self.audit.validate_soma_financial_fields(row, result)
        if errors:
            message = self._financial_error(errors)
            operation = "CORRIGIR_DATA" if self._only_date_error(errors) else "BLOQUEADO"
            return BatchDetail(row.row_number, document, message, operation, " | ".join(errors))
        payments, pending = self.audit.inspect_payment_controls(document)
        detail = BatchDetail(row.row_number, document, "PENDENTE", payments=list(payments), pending_baixas=list(pending))
        if len(payments) > 1:
            detail.result = "ERRO: pagamentos duplicados"
            detail.operation = "BLOQUEADO"
        elif len(pending) > 1 or (pending and (len(payments) != 1 or payments[0] != pending[0])):
            detail.result = "ERRO: baixa ambígua"
            detail.operation = "BLOQUEADO"
        elif (
            norm_basic(result.status) == "pago"
            and norm_basic(result.baixa) == "sim"
            and len(payments) == 1
            and not pending
        ):
            detail.result = "VALIDADO"
        elif norm_basic(result.status) == "pago" or norm_basic(result.baixa) == "sim":
            detail.result = "ERRO: estado de pagamento ambíguo"
            detail.operation = "BLOQUEADO"
        elif not payments and not pending:
            detail.operation = "CRIAR_PAGAMENTO"
        elif len(payments) == 1 and len(pending) == 1:
            detail.operation = "EXECUTAR_BAIXA"
        else:
            detail.result = "ERRO: estado de pagamento ambíguo"
            detail.operation = "BLOQUEADO"
        return detail

    def _snapshot_unchanged(
        self,
        row: ContaOrdemRow,
        header_map: dict[str, int],
        cells: list[str],
        expected_soma: str,
    ) -> bool:
        current = ContaOrdemRow.from_dict(
            row.row_number,
            {name: self._cell(cells, index) for name, index in header_map.items()},
        )
        return (
            self._cell(cells, header_map["SOMA"]) == expected_soma
            and current.doc_soma == row.doc_soma
            and current.tipo == row.tipo
            and current.data_mov == row.data_mov
            and current.descricao_soma == row.descricao_soma
            and current.importancia == row.importancia
        )

    def _flush_writes(
        self,
        pending: list[tuple[ContaOrdemRow, str, str]],
        header_map: dict[str, int],
        report: BatchReport,
    ) -> bool:
        if not pending:
            return False
        current_values = self.sheets._ws.get_all_values()
        column = header_map["SOMA"] + 1
        ready: list[tuple[ContaOrdemRow, str, str]] = []
        for row, value, expected_soma in pending:
            cells = current_values[row.row_number - 1] if row.row_number <= len(current_values) else []
            if not self._snapshot_unchanged(row, header_map, cells, expected_soma):
                report.temporary_failures += 1
                continue
            cell = f"{self.sheets._col_letter(column)}{row.row_number}"
            ready.append((row, value, cell))
        pending.clear()
        if not ready:
            return True
        self.sheets._ws.batch_update(
            [{"range": cell, "values": [[value]]} for _, value, cell in ready]
        )
        readbacks = self.sheets._ws.batch_get([cell for _, _, cell in ready])
        for (_, value, _), result in zip(ready, readbacks, strict=True):
            confirmed = bool(result and result[0]) and str(result[0][0]).strip() == value
            if not confirmed:
                report.temporary_failures += 1
                continue
            report.readbacks_confirmed += 1
            if value == "VALIDADO":
                report.validated_written += 1
            else:
                report.errors_written += 1
        return True

    def _apply_operation(self, row: ContaOrdemRow, detail: BatchDetail) -> tuple[BatchDetail, str, int, int, int]:
        created = baixas = automatic = 0
        if detail.operation == "CRIAR_PAGAMENTO":
            ok = self.audit.create_soma_payment(
                detail.document, row.data_mov, row.importancia, row.caixa, row.forma_pagamento
            )
            if not ok:
                return detail, "INCERTO", created, baixas, automatic
            created = 1
            detail = self._classify_row(row)
            if detail.result == "VALIDADO":
                automatic = 1
                return detail, "OK", created, baixas, automatic
            if detail.operation != "EXECUTAR_BAIXA":
                return detail, "OK", created, baixas, automatic
            ok = self.audit.insert_soma_baixa(detail.document, detail.payments[0], row.data_mov)
            if not ok:
                return detail, "INCERTO", created, baixas, automatic
            baixas = 1
        elif detail.operation == "EXECUTAR_BAIXA":
            ok = self.audit.insert_soma_baixa(detail.document, detail.payments[0], row.data_mov)
            if not ok:
                return detail, "INCERTO", created, baixas, automatic
            baixas = 1
        return self._classify_row(row), "OK", created, baixas, automatic

    def _record(self, report: BatchReport, detail: BatchDetail) -> None:
        report.details.append(detail)
        if not self.checkpoint_path:
            return
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        results_path = self.checkpoint_path.with_suffix(".results.jsonl")
        with results_path.open("a", encoding="utf-8") as results_file:
            results_file.write(json.dumps(asdict(detail), ensure_ascii=False) + "\n")
        self._persist_checkpoint(report, results_path)

    def _persist_checkpoint(self, report: BatchReport, results_path: Path | None = None) -> None:
        if not self.checkpoint_path:
            return
        results_path = results_path or self.checkpoint_path.with_suffix(".results.jsonl")
        checkpoint = report.to_dict()
        checkpoint.pop("details", None)
        checkpoint["processed"] = len(report.details)
        checkpoint["results_file"] = str(results_path)
        temporary = self.checkpoint_path.with_suffix(self.checkpoint_path.suffix + ".tmp")
        temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                temporary.replace(self.checkpoint_path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))

    def run(
        self,
        apply: bool = False,
        periodo: str | None = None,
        allow_financial_mutations: bool = True,
    ) -> BatchReport:
        normalized_period = self._normalize_period(periodo) if periodo else ""
        if self.checkpoint_path:
            results_path = self.checkpoint_path.with_suffix(".results.jsonl")
            if results_path.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                previous = results_path.with_name(f"{results_path.stem}.previous.{stamp}.jsonl")
                results_path.replace(previous)
        values = self.sheets._ws.get_all_values()
        if not values:
            raise RuntimeError("CONTAORDEM vazia")
        headers = [str(item).strip() for item in values[0]]
        header_map = self._header_map(headers)
        report = BatchReport(period=normalized_period, total_rows=max(0, len(values) - 1))
        records = []
        soma_at_start: dict[int, str] = {}
        document_counts = Counter()
        for number, cells in enumerate(values[1:], start=2):
            raw = {name: self._cell(cells, index) for name, index in header_map.items()}
            row = ContaOrdemRow.from_dict(number, raw)
            row_period = self._row_period(row.data_mov)
            if row_period is None:
                if not self._cell(cells, header_map["SOMA"]) and row.tipo.value in ("Entrada", "Saída"):
                    report.invalid_dates += 1
                continue
            if normalized_period and row_period != normalized_period:
                continue
            report.period_rows += 1
            soma_value = self._cell(cells, header_map["SOMA"])
            if not self._eligible_soma(soma_value):
                report.ignored_filled += 1
                continue
            if soma_value:
                report.date_error_retries += 1
            else:
                report.empty_at_start += 1
            if row.tipo.value not in ("Entrada", "Saída"):
                report.ignored_non_eligible_type += 1
                continue
            report.eligible += 1
            records.append(row)
            soma_at_start[number] = soma_value
            document_counts[normalize_document_value(row.doc_soma)] += 1
        if records and not self.authenticate():
            raise RuntimeError("Falha persistente de autenticação ao SOMA")
        pending_writes: list[tuple[ContaOrdemRow, str, str]] = []
        last_flush = self.clock()
        for row in records:
            document = normalize_document_value(row.doc_soma)
            if len(document) != 7 or not document.isdigit():
                detail = BatchDetail(row.row_number, document, "ERRO: DOC. SOMA inválido", "BLOQUEADO")
            elif document_counts[document] != 1:
                detail = BatchDetail(row.row_number, document, "ERRO: DOC. SOMA duplicado na CONTAORDEM", "BLOQUEADO")
            else:
                try:
                    detail = self._classify_row(row)
                    report.documents_consulted += 1
                except Exception as exc:  # noqa: BLE001 - isola falhas temporárias por registo
                    detail = BatchDetail(row.row_number, document, "PENDENTE", "NENHUMA", str(exc)[:300])
                    report.temporary_failures += 1
                    self._record(report, detail)
                    continue
            if apply and allow_financial_mutations and detail.operation == "CORRIGIR_DATA":
                try:
                    corrected = self.audit.update_soma_date(detail.document, row.data_mov)
                    detail = self._classify_row(row) if corrected else detail
                except Exception as exc:  # noqa: BLE001 - mutação sem confirmação bloqueia o registo
                    corrected = False
                    detail.reason = str(exc)[:300]
                if not corrected or detail.operation == "CORRIGIR_DATA":
                    detail.result = "PENDENTE"
                    detail.operation = "NENHUMA"
                    detail.reason = detail.reason or "Atualização da DATA MOV. não confirmada no SOMA"
                    report.uncertain_results += 1
                    self._record(report, detail)
                    continue
                report.dates_corrected += 1
            if detail.result.startswith("ERRO:"):
                report.definitive_errors += 1
            if detail.result == "VALIDADO":
                report.validated_without_correction += 1
            if (
                apply
                and allow_financial_mutations
                and detail.operation in ("CRIAR_PAGAMENTO", "EXECUTAR_BAIXA")
            ):
                detail, state, created, baixas, automatic = self._apply_operation(row, detail)
                report.payments_created += created
                report.baixas_executed += baixas
                report.automatic_baixas += automatic
                if state == "INCERTO":
                    report.uncertain_results += 1
                    self._record(report, detail)
                    continue
            if apply and (detail.result == "VALIDADO" or detail.result.startswith("ERRO:")):
                pending_writes.append((row, detail.result, soma_at_start[row.row_number]))
            self._record(report, detail)
            if pending_writes and self.clock() - last_flush >= self.flush_interval_seconds:
                self._flush_writes(pending_writes, header_map, report)
                self._persist_checkpoint(report)
                last_flush = self.clock()
        if self._flush_writes(pending_writes, header_map, report):
            self._persist_checkpoint(report)
        return report
