from pathlib import Path
from types import SimpleNamespace

import pytest

from domain.models import SomaSearchResult
from workflows.soma_audit_batch import SomaAuditBatchOrchestrator
from workflows.soma_audit_batch_cli import (
    build_parser,
    period_directory,
    validate_apply_confirmation,
)

HEADERS = ["DATA MOV.", "IMPORTÂNCIA", "DOC. SOMA", "TIPO", "DESCRIÇÃO SOMA", "FORMA DE PAGAMENTO", "CAIXA", "SOMA"]


def row(doc="5629124", soma="", descricao="ENERGIA ELÉTRICA N001"):
    return ["26/10/2026", "244,42", doc, "Saída", descricao, "TRANSFERÊNCIA BANCÁRIA", "MONTEPIO", soma]


class Worksheet:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []
        self.batch_updates = []
        self.batch_get_calls = []

    def get_all_values(self):
        return [list(item) for item in self.rows]

    def row_values(self, number, **kwargs):
        return list(self.rows[number - 1])

    def update(self, cell, values, **kwargs):
        self.updates.append((cell, values[0][0]))
        col = ord(cell[0]) - 65
        number = int(cell[1:]) - 1
        self.rows[number][col] = values[0][0]

    def acell(self, cell):
        col = ord(cell[0]) - 65
        number = int(cell[1:]) - 1
        return SimpleNamespace(value=self.rows[number][col])

    def batch_update(self, updates, **kwargs):
        self.batch_updates.append(updates)
        for update in updates:
            self.update(update["range"], update["values"])

    def batch_get(self, cells, **kwargs):
        self.batch_get_calls.append(cells)
        values = []
        for cell in cells:
            col = ord(cell[0]) - 65
            number = int(cell[1:]) - 1
            values.append([[self.rows[number][col]]])
        return values


class Sheets:
    def __init__(self, rows):
        self._ws = Worksheet(rows)

    @staticmethod
    def _col_letter(index):
        return chr(64 + index)


class Audit:
    def __init__(self, result=None, payments=None, pending=(), financial_errors=()):
        self.result = result or SomaSearchResult("5629124", "SAÍDA", "ENERGIA ELÉTRICA N001", "244,42", "26/10/2026", "PAGO", "SIM")
        self.payments = ["41"] if payments is None else list(payments)
        self.pending = list(pending)
        self.financial_errors = list(financial_errors)
        self.mutations = []
        self.search_calls = []

    def search_by_codigo(self, doc):
        self.search_calls.append(doc)
        return self.result

    def validate_soma_financial_fields(self, row, result):
        return list(self.financial_errors)

    def inspect_payment_controls(self, doc):
        return self.payments, self.pending

    def create_soma_payment(self, *args):
        self.mutations.append(("payment", args))
        return True

    def insert_soma_baixa(self, *args):
        self.mutations.append(("baixa", args))
        return True


def orchestrator(rows, audit=None):
    return SomaAuditBatchOrchestrator(sheets=Sheets(rows), audit=audit or Audit(), authenticate=lambda: True)


def test_dry_run_selects_only_empty_soma_and_never_mutates():
    audit = Audit()
    service = orchestrator([HEADERS, row(), row("5629125", "VALIDADO")], audit)
    report = service.run(apply=False)
    assert report.total_rows == 2
    assert report.empty_at_start == 1
    assert report.ignored_filled == 1
    assert report.details[0].result == "VALIDADO"
    assert audit.mutations == []
    assert service.sheets._ws.updates == []


def test_non_entrada_saida_is_ignored_before_authentication_or_portal_query():
    audit = Audit()
    auth_calls = []
    transfer = row(doc="Transferido")
    transfer[3] = "Transferência"
    service = SomaAuditBatchOrchestrator(
        sheets=Sheets([HEADERS, transfer]),
        audit=audit,
        authenticate=lambda: auth_calls.append(True) or True,
    )

    report = service.run(apply=False)

    assert report.empty_at_start == 1
    assert report.eligible == 0
    assert report.ignored_non_eligible_type == 1
    assert report.details == []
    assert auth_calls == []
    assert audit.search_calls == []


def test_eligible_type_with_invalid_document_is_error_without_portal_query():
    audit = Audit()
    service = orchestrator([HEADERS, row(doc="INVÁLIDO")], audit)

    report = service.run(apply=False)

    assert report.eligible == 1
    assert report.details[0].result == "ERRO: DOC. SOMA inválido"
    assert audit.search_calls == []


def test_duplicate_required_header_stops_before_authentication():
    service = orchestrator([HEADERS + ["SOMA"], row() + [""]])
    with pytest.raises(RuntimeError, match="duplicado"):
        service.run(apply=False)


@pytest.mark.parametrize(("audit", "operation", "result"), [
    (Audit(payments=(), pending=()), "CRIAR_PAGAMENTO", "PENDENTE"),
    (Audit(payments=("41",), pending=("41",)), "EXECUTAR_BAIXA", "PENDENTE"),
    (Audit(payments=("41", "42"), pending=()), "BLOQUEADO", "ERRO: pagamentos duplicados"),
    (Audit(financial_errors=("DATA (col 7): divergente",)), "CORRIGIR_DATA", "ERRO: DATA MOV. divergente"),
])
def test_dry_run_classifies_required_operation(audit, operation, result):
    audit.result.status = "EM ABERTO"
    audit.result.baixa = ""
    detail = orchestrator([HEADERS, row()], audit).run(apply=False).details[0]
    assert detail.operation == operation
    assert detail.result == result
    assert audit.mutations == []


def test_paid_document_without_identifiable_payment_is_ambiguous():
    detail = orchestrator([HEADERS, row()], Audit(payments=())).run(apply=False).details[0]
    assert detail.result == "ERRO: estado de pagamento ambíguo"


def test_apply_writes_only_soma_after_final_validation_and_reads_back():
    service = orchestrator([HEADERS, row()], Audit(payments=("41",)))
    report = service.run(apply=True)
    assert report.validated_written == 1
    assert report.readbacks_confirmed == 1
    assert service.sheets._ws.updates == [("H2", "VALIDADO")]


def test_apply_batches_sheet_writes_and_readback_without_per_row_reads():
    service = orchestrator([HEADERS, row(), row("5629125")], Audit(payments=("41",)))

    def reject_per_row_read(*args, **kwargs):
        raise AssertionError("per-row read is forbidden")

    service.sheets._ws.row_values = reject_per_row_read
    service.sheets._ws.acell = reject_per_row_read

    report = service.run(apply=True)

    assert len(service.sheets._ws.batch_updates) == 1
    assert [item["range"] for item in service.sheets._ws.batch_updates[0]] == ["H2", "H3"]
    assert service.sheets._ws.batch_get_calls == [["H2", "H3"]]
    assert report.validated_written == 1
    assert report.errors_written == 1
    assert report.readbacks_confirmed == 2


def test_apply_flushes_at_most_once_per_minute_and_once_at_end():
    clock = iter([0.0, 30.0, 61.0, 61.0, 90.0])
    sheets = Sheets([HEADERS, row(), row("5629125"), row("5629126")])
    service = SomaAuditBatchOrchestrator(
        sheets=sheets,
        audit=Audit(payments=("41",)),
        authenticate=lambda: True,
        clock=lambda: next(clock),
    )

    service.run(apply=True)

    assert len(sheets._ws.batch_updates) == 2
    assert [item["range"] for item in sheets._ws.batch_updates[0]] == ["H2", "H3"]
    assert [item["range"] for item in sheets._ws.batch_updates[1]] == ["H4"]


def test_apply_does_not_overwrite_value_changed_after_snapshot():
    service = orchestrator([HEADERS, row()], Audit(payments=("41",)))
    original = service._classify_row

    def change_sheet(*args, **kwargs):
        decision = original(*args, **kwargs)
        service.sheets._ws.rows[1][7] = "OUTRO"
        return decision

    service._classify_row = change_sheet
    report = service.run(apply=True)
    assert report.temporary_failures == 1
    assert service.sheets._ws.updates == []


class CreatingAudit(Audit):
    def __init__(self, automatic):
        super().__init__()
        self.result.status = "EM ABERTO"
        self.result.baixa = ""
        self.automatic = automatic
        self.created = False
        self.settled = False

    def inspect_payment_controls(self, doc):
        if not self.created:
            return [], []
        return ["41"], [] if self.automatic or self.settled else ["41"]

    def create_soma_payment(self, *args):
        self.created = True
        if self.automatic:
            self.result.status = "PAGO"
            self.result.baixa = "SIM"
        return super().create_soma_payment(*args)

    def insert_soma_baixa(self, *args):
        self.settled = True
        self.result.status = "PAGO"
        self.result.baixa = "SIM"
        return super().insert_soma_baixa(*args)


class DateCorrectionAudit(Audit):
    def __init__(self, succeeds=True, extra_error=False):
        errors = ["DATA (col 7): site='25/10/2026' != sheet='26/10/2026'"]
        if extra_error:
            errors.append("VALOR (col 6): site='200,00' != sheet='244,42'")
        super().__init__(payments=("41",), financial_errors=errors)
        self.result.data = "25/10/2026"
        self.succeeds = succeeds
        self.date_updates = []

    def update_soma_date(self, doc, value):
        self.date_updates.append((doc, value))
        if self.succeeds:
            self.result.data = value
            self.financial_errors = []
        return self.succeeds


@pytest.mark.parametrize(("automatic", "expected_baixas"), [(True, 0), (False, 1)])
def test_apply_reconciles_after_creation_and_only_settles_when_pending(automatic, expected_baixas):
    audit = CreatingAudit(automatic)
    report = orchestrator([HEADERS, row()], audit).run(apply=True)
    assert report.payments_created == 1
    assert report.baixas_executed == expected_baixas
    assert report.automatic_baixas == int(automatic)
    assert report.validated_written == 1


def test_apply_corrects_date_when_it_is_the_only_financial_divergence():
    audit = DateCorrectionAudit()
    service = orchestrator([HEADERS, row()], audit)

    report = service.run(apply=True)

    assert audit.date_updates == [("5629124", "26/10/2026")]
    assert report.dates_corrected == 1
    assert report.validated_written == 1
    assert audit.mutations == []


def test_apply_reprocesses_existing_date_error_and_replaces_it_with_validated():
    audit = DateCorrectionAudit()
    service = orchestrator([HEADERS, row(soma="ERRO: DATA MOV. divergente")], audit)

    report = service.run(apply=True)

    assert report.date_error_retries == 1
    assert audit.date_updates == [("5629124", "26/10/2026")]
    assert service.sheets._ws.rows[1][7] == "VALIDADO"


def test_apply_does_not_correct_date_when_another_financial_field_diverges():
    audit = DateCorrectionAudit(extra_error=True)

    report = orchestrator([HEADERS, row()], audit).run(apply=True)

    assert audit.date_updates == []
    assert report.dates_corrected == 0
    assert report.errors_written == 1


def test_unconfirmed_date_correction_blocks_sheet_and_financial_writes():
    audit = DateCorrectionAudit(succeeds=False)
    service = orchestrator([HEADERS, row()], audit)

    report = service.run(apply=True)

    assert audit.date_updates == [("5629124", "26/10/2026")]
    assert report.uncertain_results == 1
    assert service.sheets._ws.batch_updates == []
    assert audit.mutations == []


def test_checkpoint_is_updated_after_each_record(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    service = SomaAuditBatchOrchestrator(
        sheets=Sheets([HEADERS, row()]),
        audit=Audit(),
        authenticate=lambda: True,
        checkpoint_path=checkpoint,
    )
    service.run(apply=False)
    assert '"processed": 1' in checkpoint.read_text(encoding="utf-8")
    results = checkpoint.with_suffix(".results.jsonl")
    assert '"document": "5629124"' in results.read_text(encoding="utf-8")


def test_checkpoint_stays_compact_and_results_are_incremental(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    rows = [HEADERS] + [row(str(5629000 + i)) for i in range(1000)]
    service = SomaAuditBatchOrchestrator(Sheets(rows), Audit(), lambda: True, checkpoint)
    service.run(apply=False)
    assert checkpoint.stat().st_size < 2_000
    lines = checkpoint.with_suffix(".results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1000


def test_checkpoint_retries_transient_windows_replace_lock(tmp_path, monkeypatch):
    checkpoint = tmp_path / "checkpoint.json"
    original = Path.replace
    attempts = 0

    def locked_once(source, target):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("destination temporarily open")
        return original(source, target)

    monkeypatch.setattr(Path, "replace", locked_once)
    service = SomaAuditBatchOrchestrator(
        sheets=Sheets([HEADERS, row()]),
        audit=Audit(),
        authenticate=lambda: True,
        checkpoint_path=checkpoint,
    )
    service.run(apply=False)
    assert attempts == 2
    assert checkpoint.exists()


def test_cli_is_dry_run_by_default_and_apply_requires_exact_confirmation():
    parser = build_parser()
    assert parser.parse_args([]).apply is False
    with pytest.raises(SystemExit, match="--confirm APPLY-SOMA-BATCH"):
        validate_apply_confirmation(parser.parse_args(["--apply"]))
    validate_apply_confirmation(parser.parse_args(["--apply", "--confirm", "APPLY-SOMA-BATCH"]))


def test_period_filters_before_portal_and_handles_year_boundary():
    audit = Audit()
    october = row("5629124"); october[0] = "26/10/2026"
    november = row("5629125"); november[0] = "01/11/2026"
    old_year = row("5629126"); old_year[0] = "26/10/2025"
    report = orchestrator([HEADERS, october, november, old_year], audit).run(periodo="10/2026")
    assert report.period == "10/2026"
    assert report.period_rows == 1
    assert report.eligible == 1
    assert audit.search_calls == ["5629124"]


def test_available_periods_does_not_authenticate_or_query_portal():
    audit = Audit(); auth_calls = []
    rows = [HEADERS, row("5629124"), row("5629125")]
    rows[2][0] = "01/11/2026"
    service = SomaAuditBatchOrchestrator(Sheets(rows), audit, lambda: auth_calls.append(True) or True)
    assert service.available_periods() == {"10/2026": 1, "11/2026": 1}
    assert auth_calls == [] and audit.search_calls == []


def test_invalid_date_is_counted_without_portal_query():
    audit = Audit(); bad = row("5629124"); bad[0] = "invalid"
    report = orchestrator([HEADERS, bad], audit).run(periodo="10/2026")
    assert report.invalid_dates == 1
    assert report.eligible == 0
    assert audit.search_calls == []


def test_resume_preserves_previous_jsonl_without_duplicate_current_results(tmp_path):
    checkpoint = tmp_path / "checkpoint.json"
    service = SomaAuditBatchOrchestrator(Sheets([HEADERS, row()]), Audit(), lambda: True, checkpoint)
    service.run(periodo="10/2026")
    service.run(periodo="10/2026")
    assert len(list(tmp_path.glob("checkpoint.results.previous.*.jsonl"))) == 1
    assert len(checkpoint.with_suffix(".results.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_cli_period_argument():
    args = build_parser().parse_args(["--periodo", "10/2026"])
    assert args.periodo == "10/2026"


def test_period_without_documents_does_not_authenticate():
    audit = Audit(); auth_calls = []
    service = SomaAuditBatchOrchestrator(Sheets([HEADERS, row()]), audit, lambda: auth_calls.append(True) or True)
    report = service.run(periodo="09/2026")
    assert report.period_rows == 0 and report.eligible == 0
    assert auth_calls == [] and audit.search_calls == []


def test_period_storage_directories_are_independent():
    assert period_directory("10/2026") != period_directory("09/2026")
    assert period_directory("10/2026").parts[-1] == "2026-10"


def test_apply_can_write_results_while_financial_mutations_are_disabled():
    audit = Audit(payments=(), pending=())
    audit.result.status = ""
    audit.result.baixa = ""
    sheet = Sheets([HEADERS, row()])
    report = SomaAuditBatchOrchestrator(sheet, audit, lambda: True).run(
        apply=True,
        periodo="10/2026",
        allow_financial_mutations=False,
    )
    assert report.pending == 1
    assert audit.mutations == []
    assert sheet._ws.updates == []


def test_cli_supports_explicit_financial_mutation_block():
    args = build_parser().parse_args(["--periodo", "10/2026", "--no-financial-mutations"])
    assert args.no_financial_mutations is True
