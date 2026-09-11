from domain.models import ContaOrdemRow, TipoMovimento
from domain.models import SomaSearchResult
from workflows.orchestrator import DirectOrchestrator


def valid_row(**overrides):
    values = {
        "row_number": 2,
        "data_mov": "10/09/2026",
        "descricao": "Descrição original",
        "descricao_soma": "DESCRIÇÃO SOMA N001",
        "importancia": "1,00",
        "doc_soma": "",
        "tipo": TipoMovimento.ENTRADA,
        "plano_conta": "PLANO",
        "centro_custo": "CENTRO",
        "forma_pagamento": "DINHEIRO",
        "caixa": "CAIXA",
        "processo": "T_EXTRATO",
        "id_interno": "EXT001",
    }
    values.update(overrides)
    return ContaOrdemRow(**values)


def test_valid_launch_row_passes_prevalidation():
    assert DirectOrchestrator._validate_launch_row(valid_row()) is None


def test_descricao_soma_does_not_fall_back_to_descricao():
    error = DirectOrchestrator._validate_launch_row(valid_row(descricao_soma=""))
    assert error == "Campos obrigatórios ausentes: DESCRIÇÃO SOMA"


def test_amount_must_be_greater_than_zero():
    for amount in ("0", "0,00", "-1,00", "texto"):
        error = DirectOrchestrator._validate_launch_row(valid_row(importancia=amount))
        assert error == f"IMPORTÂNCIA inválida: '{amount}'"


def test_origin_identification_is_required():
    error = DirectOrchestrator._validate_launch_row(valid_row(processo="", id_interno=""))
    assert error == "Campos obrigatórios ausentes: PROCESSO, ID_INTERNO"


def test_all_required_fields_are_reported_together():
    row = valid_row(
        tipo=TipoMovimento.OUTRO,
        plano_conta="",
        centro_custo="",
        descricao_soma="",
        forma_pagamento="",
        caixa="",
    )
    error = DirectOrchestrator._validate_launch_row(row)
    assert error == (
        "Campos obrigatórios ausentes: TIPO, PLANO DE CONTA, CENTRO DE CUSTO, "
        "DESCRIÇÃO SOMA, CAIXA, FORMA DE PAGAMENTO"
    )


class FakeSheets:
    def __init__(self):
        self.validation_errors = []

    def mark_row_validation_error(self, row_idx, message):
        self.validation_errors.append((row_idx, message))


class FakeWorksheet:
    def __init__(self):
        self.updates = []

    def batch_update(self, updates):
        self.updates = updates


def test_invalid_row_is_marked_for_human_analysis_before_claim():
    orchestrator = object.__new__(DirectOrchestrator)
    orchestrator.sheets = FakeSheets()
    outcome = orchestrator.process_row(valid_row(descricao_soma=""))
    assert outcome.success is False
    assert orchestrator.sheets.validation_errors == [(2, "Campos obrigatórios ausentes: DESCRIÇÃO SOMA")]


def test_validation_error_writes_analisar_and_erro():
    from services.sheets_service import GoogleSheetsService

    sheets = object.__new__(GoogleSheetsService)
    sheets._ws = FakeWorksheet()
    sheets._headers_cache = ["DOC. SOMA", "STATUS", "DADOS DOC"]
    sheets.mark_row_validation_error(3, "Descrição obrigatória")
    assert sheets._ws.updates == [
        {"range": "A3", "values": [["Analisar"]]},
        {"range": "B3", "values": [["ERRO"]]},
        {"range": "C3", "values": [["Descrição obrigatória"]]},
    ]


def test_duplicate_marker_writes_analisar_and_duplicidade():
    from services.sheets_service import GoogleSheetsService

    sheets = object.__new__(GoogleSheetsService)
    sheets._ws = FakeWorksheet()
    sheets._headers_cache = ["DOC. SOMA", "STATUS", "DADOS DOC"]
    sheets.mark_row_duplicate(3, 2)
    assert sheets._ws.updates == [
        {"range": "A3", "values": [["Analisar"]]},
        {"range": "B3", "values": [["Duplicidade"]]},
        {"range": "C3", "values": [["Pesquisa preventiva encontrou 2 registros no SOMA"]]},
    ]


class PreventiveSheets:
    def __init__(self):
        self.duplicate = None
        self.completed = None

    def mark_row_duplicate(self, row, count):
        self.duplicate = (row, count)

    def mark_row_completed(self, **kwargs):
        self.completed = kwargs


class PreventiveAudit:
    def __init__(self, candidates):
        self.candidates = candidates
        self.payment_calls = 0

    def search_by_descricao(self, descricao, data_mov=""):
        return self.candidates

    def insert_soma_payment(self, **kwargs):
        self.payment_calls += 1
        return True

    def search_by_codigo(self, doc):
        return SomaSearchResult(doc, "Entrada", "DESCRIÇÃO SOMA N001", "1,00", "10/09/2026", "PAGO", "SIM")

    def fetch_dados_doc(self, doc):
        return "confirmado"


def candidate(code, status="PAGO", baixa="SIM"):
    return SomaSearchResult(code, "Entrada", "DESCRIÇÃO SOMA N001", "1,00", "10/09/2026", status, baixa)


def preventive_orchestrator(candidates):
    orchestrator = object.__new__(DirectOrchestrator)
    orchestrator.sheets = PreventiveSheets()
    orchestrator.audit_service = PreventiveAudit(candidates)
    return orchestrator


def test_multiple_search_results_are_marked_as_duplicate():
    orchestrator = preventive_orchestrator([candidate("10"), candidate("11")])
    outcome = orchestrator._process_claimed_row(valid_row())
    assert outcome.success is False
    assert outcome.doc_id == "Analisar"
    assert orchestrator.sheets.duplicate == (2, 2)


def test_single_paid_result_is_copied_without_payment():
    orchestrator = preventive_orchestrator([candidate("10")])
    outcome = orchestrator._process_claimed_row(valid_row())
    assert outcome.success is True
    assert outcome.doc_id == "10"
    assert orchestrator.audit_service.payment_calls == 0
    assert orchestrator.sheets.completed["doc_id"] == "10"


def test_single_open_result_is_paid_then_copied():
    orchestrator = preventive_orchestrator([candidate("10", "EM ABERTO", "")])
    outcome = orchestrator._process_claimed_row(valid_row())
    assert outcome.success is True
    assert outcome.doc_id == "10"
    assert orchestrator.audit_service.payment_calls == 1
    assert orchestrator.sheets.completed["doc_id"] == "10"
