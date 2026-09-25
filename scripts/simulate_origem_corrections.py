from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import ContaOrdemRow, norm_basic, normalize_date_str, normalize_document_value
from services.audit_service import AuditService
from services.monthly_checklist_service import (
    build_records_by_id,
    parse_decimal_money,
    pick_first_value,
    row_to_checklist,
)
from services.sheets_service import (
    EXTERNAL_SOURCE_SHEETS,
    EXTERNAL_SOURCE_SPREADSHEET_URL,
    SOURCE_SHEETS,
    GoogleSheetsService,
)


ERROR_TYPES = (
    "DOC. SOMA divergente na origem",
    "Sequencial ausente em DESCRICAO SOMA",
    "DATA divergente na origem",
    "DESCRICAO SOMA duplicada no mesmo dia",
    "Sequencial invalido em DESCRICAO SOMA",
)


@dataclass(frozen=True)
class Simulation:
    error_type: str
    row_number: int
    id_interno: str
    processo: str
    doc_soma: str
    current_field: str
    current_value: str
    proposed_value: str
    evidence: str
    confidence: str
    origem_message: str


def normalize_msg(value: Any) -> str:
    text = str(value or "")
    text = text.replace("inválido", "invalido")
    text = text.replace("DESCRIÇÃO", "DESCRICAO")
    return text


def classify_origem(value: str) -> str:
    text = normalize_msg(value)
    if "DOC. SOMA divergente na origem" in text:
        return "DOC. SOMA divergente na origem"
    if "Sequencial ausente em DESCRICAO SOMA" in text:
        return "Sequencial ausente em DESCRICAO SOMA"
    if "DATA divergente na origem" in text:
        return "DATA divergente na origem"
    if "DESCRICAO SOMA duplicada no mesmo dia" in text:
        return "DESCRICAO SOMA duplicada no mesmo dia"
    if "Sequencial invalido em DESCRICAO SOMA" in text:
        return "Sequencial invalido em DESCRICAO SOMA"
    return ""


def records_from_worksheet(worksheet) -> list[dict[str, str]]:
    values = worksheet.get_all_values()
    if not values:
        return []
    headers = [str(value).strip() for value in values[0]]
    records: list[dict[str, str]] = []
    for row in values[1:]:
        record = {}
        for idx, header in enumerate(headers):
            if header and header not in record:
                record[header] = row[idx] if idx < len(row) else ""
        records.append(record)
    return records


def load_origin_records(sheets: GoogleSheetsService, process: str) -> dict[str, list[dict[str, str]]]:
    source_name = SOURCE_SHEETS.get(norm_basic(process))
    if not source_name:
        return {}
    spreadsheet = sheets._sh
    if source_name in EXTERNAL_SOURCE_SHEETS:
        spreadsheet = sheets._gc.open_by_url(EXTERNAL_SOURCE_SPREADSHEET_URL)
    return build_records_by_id(records_from_worksheet(spreadsheet.worksheet(source_name)))


def same_amount(left: Any, right: Any) -> bool:
    try:
        return abs(parse_decimal_money(left)) == abs(parse_decimal_money(right))
    except Exception:
        return False


def soma_description_for_row(audit: AuditService, row: ContaOrdemRow) -> tuple[str, str]:
    doc = normalize_document_value(row.doc_soma)
    if doc and doc.isdigit():
        item = audit.search_by_codigo(doc)
        if item:
            return item.descricao, f"SOMA por codigo {doc}: descricao='{item.descricao}', data={item.data}, valor={item.valor}"
        return "", f"SOMA por codigo {doc}: nao encontrado"

    candidates = audit.search_by_descricao(row.descricao_soma or row.descricao, row.data_mov)
    for item in candidates:
        if same_amount(item.valor, row.importancia):
            return item.descricao, f"SOMA por descricao/data: codigo={item.codigo}, descricao='{item.descricao}'"
    if candidates:
        item = candidates[0]
        return item.descricao, f"SOMA por descricao/data sem match perfeito de valor: codigo={item.codigo}, descricao='{item.descricao}'"
    return "", "SOMA por descricao/data: nenhum candidato"


def build_simulation(
    error_type: str,
    row: ContaOrdemRow,
    origem_message: str,
    source_records: dict[str, list[dict[str, str]]],
    audit: AuditService,
) -> Simulation:
    source_record = None
    matches = source_records.get(row.id_interno, [])
    if len(matches) == 1:
        source_record = matches[0]

    evidence = ""
    current_field = ""
    current_value = ""
    proposed_value = ""
    confidence = "BAIXA"

    if error_type == "DOC. SOMA divergente na origem":
        current_field = "DOC. SOMA"
        current_value = row.doc_soma
        proposed_value = pick_first_value(source_record or {}, ("DOC. SOMA", "DOC SOMA", "CODIGO", "CÓDIGO"))
        evidence = f"Origem {row.processo} por ID_INTERNO={row.id_interno}: DOC. SOMA='{proposed_value or 'vazio'}'"
        confidence = "ALTA" if source_record is not None else "BAIXA"
    elif error_type == "DATA divergente na origem":
        current_field = "DATA MOV."
        current_value = row.data_mov
        proposed_value = normalize_date_str(pick_first_value(source_record or {}, ("DATA VALOR", "DATA MOV.", "DATA", "PAGAMENTO")))
        evidence = f"Origem {row.processo} por ID_INTERNO={row.id_interno}: DATA='{proposed_value or 'vazio'}'"
        confidence = "ALTA" if source_record is not None and proposed_value else "BAIXA"
    else:
        current_field = "DESCRICAO SOMA"
        current_value = row.descricao_soma
        proposed_value, evidence = soma_description_for_row(audit, row)
        confidence = "ALTA" if proposed_value else "BAIXA"

    return Simulation(
        error_type=error_type,
        row_number=row.row_number,
        id_interno=row.id_interno,
        processo=row.processo,
        doc_soma=row.doc_soma,
        current_field=current_field,
        current_value=current_value,
        proposed_value=proposed_value,
        evidence=evidence,
        confidence=confidence,
        origem_message=origem_message,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Simula correcoes da coluna ORIGEM sem gravar na CONTAORDEM.")
    parser.add_argument("--max-por-tipo", type=int, default=1)
    args = parser.parse_args()

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    values = sheets._ws.get_all_values()
    headers = values[0]
    indices = {norm_basic(header): index for index, header in enumerate(headers)}
    origem_idx = indices.get(norm_basic("ORIGEM"))
    if origem_idx is None:
        raise SystemExit("Coluna ORIGEM nao encontrada.")

    rows: list[tuple[ContaOrdemRow, str, str]] = []
    for row_number, values_row in enumerate(values[1:], start=2):
        raw = {
            header: values_row[idx] if idx < len(values_row) else ""
            for idx, header in enumerate(headers)
        }
        origem = values_row[origem_idx].strip() if origem_idx < len(values_row) else ""
        error_type = classify_origem(origem)
        if error_type:
            rows.append((ContaOrdemRow.from_dict(row_number, raw), origem, error_type))

    selected: list[tuple[ContaOrdemRow, str, str]] = []
    counts: dict[str, int] = {}
    for error_type in ERROR_TYPES:
        for row, origem, row_error_type in rows:
            if row_error_type != error_type:
                continue
            if counts.get(error_type, 0) >= args.max_por_tipo:
                break
            selected.append((row, origem, error_type))
            counts[error_type] = counts.get(error_type, 0) + 1

    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    if not auth.login():
        raise SystemExit("Nao foi possivel autenticar no SOMA.")
    audit = AuditService(settings=settings, http=http, sheets=sheets)

    origin_cache: dict[str, dict[str, list[dict[str, str]]]] = {}
    simulations: list[Simulation] = []
    for row, origem, error_type in selected:
        if row.processo not in origin_cache:
            origin_cache[row.processo] = load_origin_records(sheets, row.processo)
        simulations.append(
            build_simulation(
                error_type=error_type,
                row=row,
                origem_message=origem,
                source_records=origin_cache[row.processo],
                audit=audit,
            )
        )

    print("SIMULACAO_ORIGEM_CORRECTIONS")
    print(f"AMOSTRAS\t{len(simulations)}")
    for item in simulations:
        print()
        print(f"TIPO_ERRO\t{item.error_type}")
        print(f"LINHA\t{item.row_number}")
        print(f"ID_INTERNO\t{item.id_interno}")
        print(f"PROCESSO\t{item.processo}")
        print(f"DOC_SOMA\t{item.doc_soma}")
        print(f"CAMPO\t{item.current_field}")
        print(f"ATUAL\t{item.current_value}")
        print(f"PROPOSTO\t{item.proposed_value}")
        print(f"CONFIANCA\t{item.confidence}")
        print(f"EVIDENCIA\t{item.evidence}")
        print(f"ORIGEM_ATUAL\t{item.origem_message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
