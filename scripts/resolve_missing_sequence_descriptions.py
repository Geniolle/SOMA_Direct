from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gspread.utils import ValueInputOption

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import (
    ContaOrdemRow,
    clean_amount_for_comparison,
    extract_suffix_n,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
    strip_suffix_n,
)
from services.audit_service import AuditService
from services.reconciliation_resolver import ReconciliationResolver
from services.sheets_service import GoogleSheetsService


@dataclass(frozen=True)
class Plan:
    row: ContaOrdemRow
    current_desc: str
    new_desc: str
    soma_evidence: str
    similar_evidence: str
    blocked_reason: str = ""


def similar_base(left: Any, right: Any) -> bool:
    left_norm = norm_basic(strip_suffix_n(left))
    right_norm = norm_basic(strip_suffix_n(right))
    if not left_norm or not right_norm:
        return False
    return left_norm == right_norm or left_norm in right_norm or right_norm in left_norm


def values_to_rows(values: list[list[str]], headers: list[str]) -> list[ContaOrdemRow]:
    out: list[ContaOrdemRow] = []
    for row_number, row in enumerate(values[1:], start=2):
        raw = {
            header: row[idx] if idx < len(row) else ""
            for idx, header in enumerate(headers)
        }
        out.append(ContaOrdemRow.from_dict(row_number, raw))
    return out


def make_plan(row: ContaOrdemRow, origem_msg: str, audit: AuditService) -> Plan:
    current_desc = row.descricao_soma or row.descricao
    doc = normalize_document_value(row.doc_soma)
    if not doc or not doc.isdigit():
        return Plan(row, current_desc, "", "", "", f"DOC. SOMA invalido: {doc or 'vazio'}")

    soma = audit.search_by_codigo(doc)
    if soma is None:
        return Plan(row, current_desc, "", "", "", f"DOC {doc} nao encontrado no SOMA")

    soma_date = normalize_date_str(soma.data)
    row_date = normalize_date_str(row.data_mov)
    if soma_date != row_date:
        return Plan(row, current_desc, "", f"SOMA data={soma_date}", "", f"Data SOMA diferente da CONTAORDEM: {soma_date} != {row_date}")
    if clean_amount_for_comparison(soma.valor) != clean_amount_for_comparison(row.importancia):
        return Plan(row, current_desc, "", f"SOMA valor={soma.valor}", "", f"Valor SOMA diferente da CONTAORDEM: {soma.valor} != {row.importancia}")
    base = strip_suffix_n(current_desc).strip()
    soma_suffix = extract_suffix_n(soma.descricao)
    if norm_basic(soma.descricao) != norm_basic(current_desc):
        if soma_suffix is not None and norm_basic(strip_suffix_n(soma.descricao)) == norm_basic(base):
            return Plan(
                row,
                current_desc,
                soma.descricao,
                f"DOC {doc}: data={soma.data}, valor={soma.valor}, descricao='{soma.descricao}'",
                f"SOMA ja tem sequencial N{soma_suffix:03d}; sincronizar sheets",
            )
        return Plan(row, current_desc, "", f"SOMA descricao='{soma.descricao}'", "", f"Descricao SOMA atual diferente da CONTAORDEM: '{soma.descricao}' != '{current_desc}'")

    items = audit.search_by_periodo(row_date)
    similar_items = [
        item for item in items
        if normalize_document_value(item.codigo) != doc
        and similar_base(base, item.descricao)
        and extract_suffix_n(item.descricao) is not None
    ]
    used_numbers = sorted({extract_suffix_n(item.descricao) for item in similar_items if extract_suffix_n(item.descricao) is not None})
    if used_numbers:
        next_number = max(used_numbers) + 1
    else:
        next_number = 1

    new_desc = f"{base} N{next_number:03d}"
    similar_evidence = ", ".join(
        f"{item.codigo}:{item.descricao}" for item in similar_items[:8]
    ) or "Nenhum semelhante sequenciado na mesma data; iniciar N001"
    soma_evidence = f"DOC {doc}: data={soma.data}, valor={soma.valor}, descricao='{soma.descricao}'"
    return Plan(row, current_desc, new_desc, soma_evidence, similar_evidence)


def update_sheet_soma_description(sheets: GoogleSheetsService, doc: str, new_desc: str) -> bool:
    ws = sheets._sh.worksheet(sheets.settings.sheet_soma)
    values = ws.get_all_values()
    if not values:
        return False
    headers = [str(header).strip() for header in values[0]]
    indices = {norm_basic(header): idx for idx, header in enumerate(headers)}
    code_idx = indices.get(norm_basic("CODIGO"))
    desc_idx = indices.get(norm_basic("DESCRIÇÃO"))
    if code_idx is None or desc_idx is None:
        raise RuntimeError("Sheet SOMA precisa das colunas CODIGO e DESCRIÇÃO")

    matches: list[int] = []
    doc_norm = normalize_document_value(doc)
    for row_number, row in enumerate(values[1:], start=2):
        code = normalize_document_value(row[code_idx] if code_idx < len(row) else "")
        if code == doc_norm:
            matches.append(row_number)
    if len(matches) != 1:
        raise RuntimeError(f"DOC {doc_norm} encontrado {len(matches)} vez(es) na sheet SOMA")

    cell = f"{sheets._col_letter(desc_idx + 1)}{matches[0]}"
    ws.update(cell, [[new_desc]], value_input_option=ValueInputOption.user_entered)
    return True


def update_contaordem_description(sheets: GoogleSheetsService, row_number: int, new_desc: str) -> None:
    headers = sheets.get_headers()
    indices = {norm_basic(header): idx for idx, header in enumerate(headers)}
    desc_idx = indices.get(norm_basic("DESCRIÇÃO SOMA"))
    if desc_idx is None:
        raise RuntimeError("CONTAORDEM precisa da coluna DESCRIÇÃO SOMA")
    cell = f"{sheets._col_letter(desc_idx + 1)}{row_number}"
    sheets._ws.update(cell, [[new_desc]], value_input_option=ValueInputOption.user_entered)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve Sequencial ausente em DESCRIÇÃO SOMA.")
    parser.add_argument("--apply", action="store_true", help="Atualiza SOMA, CONTAORDEM e sheet SOMA.")
    args = parser.parse_args()

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    values = sheets._ws.get_all_values()
    headers = [str(header).strip() for header in values[0]]
    indices = {norm_basic(header): idx for idx, header in enumerate(headers)}
    origem_idx = indices.get(norm_basic("ORIGEM"))
    if origem_idx is None:
        raise RuntimeError("Coluna ORIGEM nao encontrada")

    rows = values_to_rows(values, headers)
    target_rows: list[tuple[ContaOrdemRow, str]] = []
    for row, raw_values in zip(rows, values[1:]):
        origem_msg = raw_values[origem_idx].strip() if origem_idx < len(raw_values) else ""
        if "Sequencial ausente em" in origem_msg:
            target_rows.append((row, origem_msg))

    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    if not auth.login():
        raise SystemExit("Nao foi possivel autenticar no SOMA.")
    audit = AuditService(settings=settings, http=http, sheets=sheets)
    resolver = ReconciliationResolver(settings, http, sheets, audit)

    plans = [make_plan(row, origem_msg, audit) for row, origem_msg in target_rows]
    print("RESOLVE_SEQUENCIAL_AUSENTE")
    print(f"TOTAL\t{len(plans)}")
    print(f"MODO\t{'APPLY' if args.apply else 'SIMULACAO'}")

    applied = 0
    blocked = 0
    for plan in plans:
        row = plan.row
        print()
        print(f"LINHA\t{row.row_number}")
        print(f"ID_INTERNO\t{row.id_interno}")
        print(f"PROCESSO\t{row.processo}")
        print(f"DOC_SOMA\t{row.doc_soma}")
        print(f"ATUAL\t{plan.current_desc}")
        print(f"PROPOSTO\t{plan.new_desc}")
        print(f"SOMA\t{plan.soma_evidence}")
        print(f"SEMELHANTES\t{plan.similar_evidence}")
        if plan.blocked_reason:
            blocked += 1
            print(f"STATUS\tBLOQUEADO")
            print(f"MOTIVO\t{plan.blocked_reason}")
            continue

        if args.apply:
            ok = resolver.update_soma_description(row.doc_soma, plan.new_desc)
            if not ok:
                blocked += 1
                print("STATUS\tBLOQUEADO")
                print("MOTIVO\tFalha ao atualizar/validar descricao no SOMA")
                continue
            update_contaordem_description(sheets, row.row_number, plan.new_desc)
            update_sheet_soma_description(sheets, row.doc_soma, plan.new_desc)
            applied += 1
            time.sleep(0.2)
            print("STATUS\tAPLICADO")
        else:
            print("STATUS\tSIMULADO")

    print()
    print(f"APLICADOS\t{applied}")
    print(f"BLOQUEADOS\t{blocked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
