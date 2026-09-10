from domain.models import ContaOrdemRow, TipoMovimento
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
