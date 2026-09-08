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


def test_audit_row_cascade_level_1_direct():
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=10,
        data_mov="04/07/2026",
        descricao="PAGAMENTO FORNECEDOR",
        importancia="150,00",
        doc_soma="5500111",
        tipo=TipoMovimento.SAIDA,
    )
    res = SomaSearchResult(
        codigo="5500111",
        tipo="SAÍDA",
        descricao="PAGAMENTO FORNECEDOR",
        valor="150,00",
        data="04/07/2026",
    )
    outcome = service.audit_row_cascade(row, date_batch_items=[res])
    assert outcome.confirmed is True
    assert outcome.level_resolved == 1
    assert outcome.auditoria == "Confirmado"
    assert outcome.new_doc == "5500111"


def test_audit_row_cascade_level_1_transfer():
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=11,
        data_mov="04/07/2026",
        descricao="TRANSFERENCIA ENTRE CONTAS",
        importancia="500,00",
        doc_soma="TRANSFERIDO",
        tipo=TipoMovimento.TRANSFERENCIA,
    )
    outcome = service.audit_row_cascade(row)
    assert outcome.confirmed is True
    assert outcome.level_resolved == 1
    assert outcome.auditoria == "Confirmado"


def test_audit_row_cascade_level_2_date_batch():
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=12,
        data_mov="04/07/2026",
        descricao="PAGAMENTO AGUA",
        importancia="45,20",
        doc_soma="9999999",
        tipo=TipoMovimento.SAIDA,
    )
    batch_item = SomaSearchResult(
        codigo="5500222",
        tipo="SAÍDA",
        descricao="PAGAMENTO AGUA N001",
        valor="45,20",
        data="04/07/2026",
    )
    used_codes = set()
    outcome = service.audit_row_cascade(row, date_batch_items=[batch_item], used_soma_codes=used_codes)
    assert outcome.confirmed is True
    assert outcome.corrected is True
    assert outcome.level_resolved == 2
    assert outcome.auditoria == "Corrigido"
    assert outcome.new_doc == "5500222"
    assert "5500222" in used_codes


def test_audit_row_cascade_level_3_semantic():
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=13,
        data_mov="04/07/2026",
        descricao="SUPERMERCADO CONTINENTE N001",
        importancia="100,00",
        doc_soma="1234567",
        tipo=TipoMovimento.SAIDA,
    )
    item_a = SomaSearchResult(
        codigo="5500333",
        tipo="SAÍDA",
        descricao="PADARIA DO BAIRRO",
        valor="100,00",
        data="04/07/2026",
    )
    item_b = SomaSearchResult(
        codigo="5500444",
        tipo="SAÍDA",
        descricao="SUPERMERCADO CONTINENTE N002",
        valor="100,00",
        data="04/07/2026",
    )
    used_codes = set()
    outcome = service.audit_row_cascade(row, date_batch_items=[item_a, item_b], used_soma_codes=used_codes)
    assert outcome.confirmed is True
    assert outcome.corrected is True
    assert outcome.level_resolved == 3
    assert outcome.new_doc == "5500444"
    assert "5500444" in used_codes


def test_audit_row_cascade_level_4_origin_missing():
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=14,
        data_mov="04/07/2026",
        descricao="DESPESA SEM SOMA",
        importancia="30,00",
        doc_soma="",
        tipo=TipoMovimento.SAIDA,
    )
    outcome = service.audit_row_cascade(row, date_batch_items=[], origin_doc="")
    assert outcome.confirmed is False
    assert outcome.level_resolved == 4
    assert outcome.auditoria == "Pendente lançamento SOMA"


def test_is_entrada_ou_saida():
    from domain.models import is_entrada_ou_saida, TipoMovimento
    assert is_entrada_ou_saida("Entrada") is True
    assert is_entrada_ou_saida("Saída") is True
    assert is_entrada_ou_saida("saida") is True
    assert is_entrada_ou_saida(TipoMovimento.ENTRADA) is True
    assert is_entrada_ou_saida(TipoMovimento.SAIDA) is True
    assert is_entrada_ou_saida("Transferência") is False
    assert is_entrada_ou_saida("Cartão") is False
    assert is_entrada_ou_saida("MVV") is False
    assert is_entrada_ou_saida(TipoMovimento.MVV) is False
    assert is_entrada_ou_saida(TipoMovimento.CARTAO) is False
    assert is_entrada_ou_saida(TipoMovimento.TRANSFERENCIA) is False
    assert is_entrada_ou_saida("") is False


def test_contaordem_from_dict_types():
    from domain.models import ContaOrdemRow, TipoMovimento
    row_ent = ContaOrdemRow.from_dict(1, {"TIPO": "Entrada"})
    assert row_ent.tipo == TipoMovimento.ENTRADA

    row_sai = ContaOrdemRow.from_dict(2, {"TIPO": "Saída"})
    assert row_sai.tipo == TipoMovimento.SAIDA

    row_mvv = ContaOrdemRow.from_dict(3, {"TIPO": "MVV"})
    assert row_mvv.tipo == TipoMovimento.MVV

    row_cartao = ContaOrdemRow.from_dict(4, {"TIPO": "Cartão"})
    assert row_cartao.tipo == TipoMovimento.CARTAO

    row_transf = ContaOrdemRow.from_dict(5, {"TIPO": "Transferência"})
    assert row_transf.tipo == TipoMovimento.TRANSFERENCIA


def test_audit_row_ignores_non_entrada_saida():
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=5,
        data_mov="04/07/2026",
        descricao="TRANSFERENCIA INTERNA",
        importancia="100,00",
        doc_soma="MVV",
        tipo=TipoMovimento.MVV,
    )
    outcome = service.audit_row(row)
    assert outcome.analyzed is False
    assert outcome.confirmed is True


