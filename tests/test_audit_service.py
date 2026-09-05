import pytest
from domain.models import (
    ContaOrdemRow,
    SomaSearchResult,
    TipoMovimento,
    clean_amount_for_comparison,
    format_amount_for_input,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
    strip_suffix_n,
    validate_dados_doc,
)
from services.audit_service import AuditService


class FakeHttp:
    pass


class FakeSheets:
    pass


def test_normalization_functions():
    assert normalize_document_value("5449937.0") == "5449937"
    assert normalize_document_value("5449937") == "5449937"
    
    assert format_amount_for_input("4,77") == "4,77"
    assert format_amount_for_input("4.77") == "4,77"
    assert format_amount_for_input("100") == "100,00"
    
    assert clean_amount_for_comparison("EUR 4,77") == "4,77"
    assert clean_amount_for_comparison("100.00 €") == "100,00"
    
    assert normalize_date_str("04/07/2026") == "04/07/2026"
    assert normalize_date_str("2026-07-04") == "04/07/2026"
    
    assert strip_suffix_n("PAGAMENTO FORNECEDOR N001") == "PAGAMENTO FORNECEDOR"
    assert strip_suffix_n("DESPESAS BANCARIAS N12") == "DESPESAS BANCARIAS"


def test_validate_dados_doc():
    doc_text = "Registrado(a) em: 19/08/2026 às 22:32:21,Verbo Café, DINHEIRO . Baixa realizada por USERJOB"
    valido, err = validate_dados_doc(doc_text, sheet_caixa="VERBO CAFÉ", sheet_forma="DINHEIRO")
    assert valido is True
    assert err is None

    # Mismatch Caixa
    valido, err = validate_dados_doc(doc_text, sheet_caixa="BANCO MONTEPIO", sheet_forma="DINHEIRO")
    assert valido is False
    assert "CAIXA divergente" in err

    # Mismatch Forma
    valido, err = validate_dados_doc(doc_text, sheet_caixa="VERBO CAFÉ", sheet_forma="TRANSFERÊNCIA BANCÁRIA")
    assert valido is False
    assert "FORMA DE PAGAMENTO divergente" in err


def test_validate_soma_record():
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=10,
        data_mov="04/07/2026",
        descricao="PAGAMENTO FORNECEDOR N001",
        importancia="4,77",
        doc_soma="5449937",
        tipo=TipoMovimento.SAIDA,
        plano_conta="FORNECEDORES",
        centro_custo="VERBO CAFE",
        descricao_soma="PAGAMENTO FORNECEDOR N001",
        forma_pagamento="DINHEIRO",
        caixa="VERBO CAFÉ",
    )
    res = SomaSearchResult(
        codigo="5449937",
        tipo="SAÍDA",
        descricao="PAGAMENTO FORNECEDOR N001",
        valor="4,77",
        data="04/07/2026",
        status="PAGO",
        baixa="SIM",
    )
    status, inconsistencias = service.validate_soma_record(row, res)
    assert status == "Confirmado"
    assert not inconsistencias


def test_matches_ignoring_code_and_suffix():
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=10,
        data_mov="04/07/2026",
        descricao="PAGAMENTO FORNECEDOR N001",
        importancia="4,77",
        doc_soma="1111111",
        tipo=TipoMovimento.SAIDA,
        plano_conta="FORNECEDORES",
        centro_custo="VERBO CAFE",
        descricao_soma="PAGAMENTO FORNECEDOR N001",
        forma_pagamento="DINHEIRO",
        caixa="VERBO CAFÉ",
    )
    # Result with another code and another suffix N002
    res = SomaSearchResult(
        codigo="5449937",
        tipo="SAÍDA",
        descricao="PAGAMENTO FORNECEDOR N002",
        valor="4,77",
        data="04/07/2026",
        status="PAGO",
        baixa="SIM",
    )
    matched, desc_differs = service.matches_ignoring_code_and_suffix(row, res)
    assert matched is True
    assert desc_differs is True
