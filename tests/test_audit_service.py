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


def test_audit_row_three_phase_sequence(monkeypatch):
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=50,
        data_mov="06/09/2026",
        descricao="TRF.CRED DANIEL LUZ",
        importancia="120,00",
        doc_soma="5061929",
        tipo=TipoMovimento.ENTRADA,
        descricao_soma="DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N001",
        forma_pagamento="TRANSFERÊNCIA BANCÁRIA",
        caixa="CAIXA MONTEPIO",
    )

    calls = []

    def mock_search_desc(desc, data_mov=""):
        calls.append(("fase1_desc", desc, data_mov))
        # Fase 1 retorna candidato compatível
        return [
            SomaSearchResult(
                codigo="5500999",
                tipo="ENTRADA",
                descricao="DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N001",
                valor="120,00",
                data="06/09/2026",
                status="PAGO",
                baixa="SIM",
            )
        ]

    def mock_search_periodo(data_mov):
        calls.append(("fase2_periodo", data_mov))
        return []

    def mock_search_codigo(doc_id):
        calls.append(("fase3_codigo", doc_id))
        return None

    monkeypatch.setattr(service, "search_by_descricao", mock_search_desc)
    monkeypatch.setattr(service, "search_by_periodo", mock_search_periodo)
    monkeypatch.setattr(service, "search_by_codigo", mock_search_codigo)
    monkeypatch.setattr(service, "fetch_dados_doc", lambda doc: "Registrado(a) em: 06/09/2026, CAIXA MONTEPIO, TRANSFERÊNCIA BANCÁRIA")

    outcome = service.audit_row(row)
    # Deve encontrar na Fase 1 e NÃO chamar Fase 2 nem Fase 3!
    assert outcome.corrected is True
    assert outcome.new_doc == "5500999"
    assert any(c[0] == "fase1_desc" for c in calls)
    assert not any(c[0] == "fase2_periodo" for c in calls)
    assert not any(c[0] == "fase3_codigo" for c in calls)


def test_audit_row_corrects_divergent_sequential_in_sheet(monkeypatch):
    """
    Testa o caso específico onde o DOC. SOMA está preenchido na folha (ex: 4580356),
    a folha tem sequencial N007, mas no SOMA o mesmo DOC tem sequencial N008.
    Todos os dados financeiros (Data, Valor, Tipo, Status=PAGO, Baixa=SIM, Caixa, Forma) batem 100%.
    O auditor deve retornar is_correction=True, new_doc=4580356 e new_desc com N008!
    """
    service = AuditService(settings=None, http=None, sheets=None)
    row = ContaOrdemRow(
        row_number=2,
        data_mov="06/09/2025",
        descricao="DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N007",
        importancia="25,00",
        doc_soma="4580356",
        tipo=TipoMovimento.ENTRADA,
        descricao_soma="DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N007",
        forma_pagamento="TRANSFERÊNCIA BANCÁRIA",
        caixa="MONTEPIO GERAL",
    )

    soma_doc_4580356 = SomaSearchResult(
        codigo="4580356",
        tipo="ENTRADA",
        descricao="DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N008",
        valor="25,00",
        data="06/09/2025",
        status="PAGO",
        baixa="SIM",
    )

    monkeypatch.setattr(service, "search_by_descricao", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "search_by_periodo", lambda *args, **kwargs: [soma_doc_4580356])
    monkeypatch.setattr(service, "search_by_codigo", lambda doc_id: soma_doc_4580356 if doc_id == "4580356" else None)
    monkeypatch.setattr(
        service,
        "fetch_dados_doc",
        lambda doc: "Registrado(a) em: 06/09/2025 às 10:00:00, MONTEPIO GERAL, TRANSFERÊNCIA BANCÁRIA . Baixa realizada por USERJOB",
    )

    outcome = service.audit_row(row)
    assert outcome.corrected is True
    assert outcome.new_doc == "4580356"
    assert outcome.new_desc == "DÍZIMOS E OFERTAS (TRANSFERENCIA BANCARIA) N008"
    assert outcome.inconsistent is False




