from types import SimpleNamespace
from typing import Dict
import pytest

from domain.models import ContaOrdemRow, TipoMovimento
from services.sheets_service import (
    EXTERNAL_SOURCE_SPREADSHEET_URL,
    GoogleSheetsService,
    OriginConfig,
    build_default_origin_registry,
)


class FakeWorksheet:
    def __init__(self, values):
        self.values = [list(row) for row in values]
        self.updates = []

    def get_all_values(self):
        return self.values

    def update(self, cell, values):
        self.updates.append((cell, values))

    def batch_update(self, updates, **kwargs):
        self.updates.append(("batch", updates, kwargs))

    def row_values(self, row_idx, **kwargs):
        if 1 <= row_idx <= len(self.values):
            return self.values[row_idx - 1]
        return []


class FakeSpreadsheet:
    def __init__(self, worksheets):
        self.worksheets = worksheets

    def worksheet(self, name):
        if name not in self.worksheets:
            raise KeyError(f"Sheet '{name}' not found")
        return self.worksheets[name]


def make_service(origin=None, external_worksheets=None):
    service = GoogleSheetsService.__new__(GoogleSheetsService)
    primary_sheets = {}
    if origin is not None:
        primary_sheets["T_EXTRATO"] = origin
        primary_sheets["DÍZIMOS/OFERTAS"] = origin
        primary_sheets["SAÍDAS"] = origin
    service._sh = FakeSpreadsheet(primary_sheets)
    service._ws = FakeWorksheet([])
    service._gc = SimpleNamespace()
    service.settings = SimpleNamespace(
        user_job_id="JOB",
        spreadsheet_url="https://docs.google.com/spreadsheets/d/primary/edit",
        app_verbo_cafe_spreadsheet_url=EXTERNAL_SOURCE_SPREADSHEET_URL,
    )
    service._headers_cache = [
        "DATA MOV.",
        "DESCRIÇÃO",
        "IMPORTÂNCIA",
        "DOC. SOMA",
        "TIPO",
        "PROCESSO",
        "ID_INTERNO",
        "LINK",
        "STATUS",
        "AUDITORIA",
        "IDUSER",
        "TIMESTAMP",
    ]
    service._spreadsheet_cache = {
        service.settings.spreadsheet_url: service._sh,
    }
    if external_worksheets:
        ext_sh = FakeSpreadsheet(external_worksheets)
        service.register_spreadsheet(EXTERNAL_SOURCE_SPREADSHEET_URL, ext_sh)
    service._origin_registry = build_default_origin_registry(
        default_spreadsheet_url=service.settings.spreadsheet_url,
        app_verbo_cafe_url=EXTERNAL_SOURCE_SPREADSHEET_URL,
    )
    return service


def test_completed_row_updates_origin_by_id_before_contaordem():
    origin = FakeWorksheet([
        ["DESCRIÇÃO", "ID_INTERNO", "DOC. SOMA"],
        ["Movimento", "EXT001", ""],
    ])
    service = make_service(origin)

    service.mark_row_completed(
        row_idx=8,
        doc_id="5500123",
        processo="T_EXTRATO",
        id_interno="EXT001",
    )

    assert origin.updates == [("C2", [["5500123"]])]
    assert service._ws.updates[0][0] == "batch"
    updates = {item["range"]: item["values"] for item in service._ws.updates[0][1]}
    assert updates["D8"] == [["5500123"]]
    assert updates["H8"] == [[
        '=HYPERLINK("https://verbodavida.info/IVV/?mod=ivv&exec=entradas_saidas_dados&ID=5500123";"ACESSAR SOMA")'
    ]]
    assert service._ws.updates[0][2]["value_input_option"] == "USER_ENTERED"


def test_origin_update_rejects_duplicate_internal_id():
    origin = FakeWorksheet([
        ["ID INTERNO", "DOC. SOMA"],
        ["EXT001", ""],
        ["EXT001", ""],
    ])
    service = make_service(origin)

    with pytest.raises(ValueError, match="encontrado 2 vez"):
        service._update_origin_doc("T_EXTRATO", "EXT001", "5500123")
    assert origin.updates == []


def test_origin_update_overwrites_another_document():
    origin = FakeWorksheet([
        ["ID_INTERNO", "DOC. SOMA"],
        ["EXT001", "4400000"],
    ])
    service = make_service(origin)

    service._update_origin_doc("T_EXTRATO", "EXT001", "5500123")

    assert origin.updates == [("B2", [["5500123"]])]


@pytest.mark.parametrize("doc_id", ["", "123456", "12345678", "ABC1234", "Analisar"])
def test_completed_row_only_accepts_exactly_seven_numeric_digits(doc_id):
    origin = FakeWorksheet([
        ["ID_INTERNO", "DOC. SOMA"],
        ["EXT001", "4400000"],
    ])
    service = make_service(origin)

    with pytest.raises(ValueError, match="exatamente 7 dígitos"):
        service.mark_row_completed(
            row_idx=8,
            doc_id=doc_id,
            processo="T_EXTRATO",
            id_interno="EXT001",
        )

    assert origin.updates == []
    assert service._ws.updates == []


def test_origin_in_external_spreadsheet_verbo_cafe():
    """Valida origem externa em spreadsheet diferente (AppVerboCafé: Financeiro e VC_VENDAS)."""
    ws_fin = FakeWorksheet([
        ["DATA", "DESCRIÇÃO", "ID_INTERNO", "DOC. SOMA"],
        ["01/02/2026", "Café compra", "VCS0000000090", ""],
    ])
    ws_vendas = FakeWorksheet([
        ["DATA", "PRODUTO", "DOC. SOMA", "ID_INTERNO"],
        ["02/02/2026", "Café venda", "", "VCE0000000607"],
    ])
    service = make_service(external_worksheets={"Financeiro": ws_fin, "VC_VENDAS": ws_vendas})

    # Atualiza Financeiro
    service._update_origin_doc("Financeiro", "VCS0000000090", "5524521")
    assert ws_fin.updates == [("D2", [["5524521"]])]

    # Atualiza VC_VENDAS
    service._update_origin_doc("VC_VENDAS", "VCE0000000607", "5518021")
    assert ws_vendas.updates == [("C2", [["5518021"]])]


def test_unconfigured_process_raises_value_error():
    service = make_service()
    with pytest.raises(ValueError, match="PROCESSO sem planilha de origem configurada: 'PROCESSO_DESCONHECIDO'"):
        service._update_origin_doc("PROCESSO_DESCONHECIDO", "ID123", "5500123")


def test_internal_id_not_found_raises_value_error():
    origin = FakeWorksheet([
        ["ID_INTERNO", "DOC. SOMA"],
        ["EXT001", ""],
    ])
    service = make_service(origin)
    with pytest.raises(ValueError, match="encontrado 0 vez"):
        service._update_origin_doc("T_EXTRATO", "NAO_EXISTE", "5500123")
    assert origin.updates == []


def test_mark_row_completed_auto_reads_contaordem_row_when_not_passed():
    """Quando processo e id_interno são omitidos, recupera da linha da CONTAORDEM."""
    origin = FakeWorksheet([
        ["DESCRIÇÃO", "ID_INTERNO", "DOC. SOMA"],
        ["Movimento", "EXT999", ""],
    ])
    service = make_service(origin)

    headers = [
        "DATA MOV.", "DESCRIÇÃO", "IMPORTÂNCIA", "DOC. SOMA",
        "TIPO", "PROCESSO", "ID_INTERNO", "LINK", "STATUS", "AUDITORIA", "IDUSER", "TIMESTAMP"
    ]
    service._ws = FakeWorksheet([
        headers,
        ["01/02/2026", "Teste", "10,00", "", "Entrada", "T_EXTRATO", "EXT999", "", "PENDENTE", "", "", ""],
    ])

    service.mark_row_completed(row_idx=2, doc_id="5599999")

    # Garante que atualizou a origem com o ID obtido da CONTAORDEM
    assert origin.updates == [("C2", [["5599999"]])]


def test_sync_contaordem_row_to_origin():
    """Testa a sincronização de volta a partir de uma linha existente da CONTAORDEM."""
    ws_fin = FakeWorksheet([
        ["DATA", "DESCRIÇÃO", "ID_INTERNO", "DOC. SOMA"],
        ["01/02/2026", "Café compra", "VCS0000000090", ""],
    ])
    service = make_service(external_worksheets={"Financeiro": ws_fin})

    headers = [
        "DATA MOV.", "DESCRIÇÃO", "IMPORTÂNCIA", "DOC. SOMA",
        "TIPO", "PROCESSO", "ID_INTERNO", "LINK", "STATUS", "AUDITORIA", "IDUSER", "TIMESTAMP"
    ]
    service._ws = FakeWorksheet([
        headers,
        ["01/02/2026", "Café", "50,00", "5524521", "Saída", "Financeiro", "VCS0000000090", "", "VALIDADO", "Confirmado", "USER", "01/02/2026 10:00:00"],
    ])

    synced = service.sync_contaordem_row_to_origin(row_idx=2)
    assert synced is True
    assert ws_fin.updates == [("D2", [["5524521"]])]


def test_custom_origin_registration():
    """Valida o registro e resolução extensível de novos sistemas/spreadsheets sem alterar o código base."""
    ws_custom = FakeWorksheet([
        ["ID_REGISTRO", "VALOR", "DOC. SOMA"],
        ["CUSTOM_001", "125,00", ""],
    ])
    service = make_service()
    custom_url = "https://docs.google.com/spreadsheets/d/novo_sistema_url/edit"
    custom_sh = FakeSpreadsheet({"Lançamentos Externos": ws_custom})
    service.register_spreadsheet(custom_url, custom_sh)

    service.register_origin(OriginConfig(
        processo="SISTEMA_NOVO",
        sheet_name="Lançamentos Externos",
        spreadsheet_url=custom_url,
        id_column_name="ID_REGISTRO",
        doc_column_name="DOC. SOMA",
    ))

    service._update_origin_doc("SISTEMA_NOVO", "CUSTOM_001", "5577777")
    assert ws_custom.updates == [("C2", [["5577777"]])]
