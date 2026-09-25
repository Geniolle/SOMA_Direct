from __future__ import annotations

import sys
import argparse
from pathlib import Path
from typing import Any

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import ContaOrdemRow, clean_amount_for_comparison, norm_basic, normalize_date_str, normalize_document_value
from services.audit_service import AuditService
from services.monthly_checklist_service import build_records_by_id, pick_first_value
from services.sheets_service import (
    EXTERNAL_SOURCE_SHEETS,
    EXTERNAL_SOURCE_SPREADSHEET_URL,
    SOURCE_SHEETS,
    GoogleSheetsService,
)


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


def cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def same_text(left: Any, right: Any) -> bool:
    return norm_basic(left) == norm_basic(right)


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida e opcionalmente corrige DOC. SOMA divergente na origem.")
    parser.add_argument("--apply", action="store_true", help="Atualiza DOC. SOMA na origem quando SOMA confirma data, valor e descricao.")
    args = parser.parse_args()

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    values = sheets._ws.get_all_values()
    if not values:
        raise SystemExit("CONTAORDEM vazia.")

    headers = [str(header).strip() for header in values[0]]
    indices = {norm_basic(header): idx for idx, header in enumerate(headers)}
    origem_idx = indices.get(norm_basic("ORIGEM"))
    if origem_idx is None:
        raise SystemExit("Coluna ORIGEM nao encontrada.")

    candidates: list[tuple[ContaOrdemRow, str]] = []
    for row_number, values_row in enumerate(values[1:], start=2):
        origem_msg = cell(values_row, origem_idx)
        if "DOC. SOMA divergente na origem" not in origem_msg:
            continue
        raw = {
            header: values_row[idx] if idx < len(values_row) else ""
            for idx, header in enumerate(headers)
        }
        candidates.append((ContaOrdemRow.from_dict(row_number, raw), origem_msg))

    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    if not auth.login():
        raise SystemExit("Nao foi possivel autenticar no SOMA.")
    audit = AuditService(settings=settings, http=http, sheets=sheets)

    origin_cache: dict[str, dict[str, list[dict[str, str]]]] = {}
    print("VALIDACAO_DOC_SOMA_DIVERGENTE")
    print(f"TOTAL_CANDIDATOS\t{len(candidates)}")

    aptos = 0
    bloqueados = 0
    atualizados = 0
    for row, origem_msg in candidates:
        if row.processo not in origin_cache:
            origin_cache[row.processo] = load_origin_records(sheets, row.processo)
        source_matches = origin_cache[row.processo].get(row.id_interno, [])
        source_record = source_matches[0] if len(source_matches) == 1 else {}
        source_doc = pick_first_value(source_record, ("DOC. SOMA", "DOC SOMA", "CODIGO", "CÓDIGO"))

        doc = normalize_document_value(row.doc_soma)
        soma = audit.search_by_codigo(doc)
        checks: list[str] = []
        if not doc or not doc.isdigit():
            checks.append(f"DOC CONTAORDEM invalido: {doc or 'vazio'}")
        if soma is None:
            checks.append(f"DOC {doc or 'vazio'} nao encontrado no SOMA")
        else:
            soma_date = normalize_date_str(soma.data)
            row_date = normalize_date_str(row.data_mov)
            if soma_date != row_date:
                checks.append(f"DATA SOMA divergente: soma={soma_date or 'vazio'} contaordem={row_date or 'vazio'}")
            if clean_amount_for_comparison(soma.valor) != clean_amount_for_comparison(row.importancia):
                checks.append(f"VALOR SOMA divergente: soma={soma.valor or 'vazio'} contaordem={row.importancia or 'vazio'}")
            if not same_text(soma.descricao, row.descricao_soma or row.descricao):
                checks.append(f"DESCRICAO SOMA divergente: soma='{soma.descricao}' contaordem='{row.descricao_soma or row.descricao}'")

        status = "APTO_ATUALIZAR_ORIGEM" if not checks and len(source_matches) == 1 else "BLOQUEADO"
        if len(source_matches) != 1:
            checks.append(f"ID_INTERNO na origem encontrado {len(source_matches)} vez(es)")
            status = "BLOQUEADO"

        if status == "APTO_ATUALIZAR_ORIGEM":
            aptos += 1
            if args.apply:
                sheets._update_origin_doc(row.processo, row.id_interno, row.doc_soma)
                atualizados += 1
        else:
            bloqueados += 1

        print()
        print(f"LINHA\t{row.row_number}")
        print(f"STATUS\t{status}")
        print(f"ID_INTERNO\t{row.id_interno}")
        print(f"PROCESSO\t{row.processo}")
        print(f"DOC_CONTAORDEM\t{row.doc_soma}")
        print(f"DOC_ORIGEM_ATUAL\t{source_doc or 'vazio'}")
        print(f"ACAO_SIMULADA\tAtualizar DOC. SOMA da origem para {row.doc_soma}" if status == "APTO_ATUALIZAR_ORIGEM" else "ACAO_SIMULADA\tNenhuma")
        if soma is not None:
            print(f"SOMA_DATA\t{soma.data}")
            print(f"SOMA_VALOR\t{soma.valor}")
            print(f"SOMA_DESCRICAO\t{soma.descricao}")
        print(f"CONTAORDEM_DATA\t{row.data_mov}")
        print(f"CONTAORDEM_VALOR\t{row.importancia}")
        print(f"CONTAORDEM_DESCRICAO_SOMA\t{row.descricao_soma or row.descricao}")
        print(f"ORIGEM_MSG\t{origem_msg}")
        if checks:
            print("MOTIVOS_BLOQUEIO\t" + " | ".join(checks))

    print()
    print(f"APTOS\t{aptos}")
    print(f"BLOQUEADOS\t{bloqueados}")
    print(f"ATUALIZADOS\t{atualizados}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
