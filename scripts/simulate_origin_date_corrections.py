import argparse
import re
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
from domain.models import ContaOrdemRow, clean_amount_for_comparison, norm_basic, normalize_date_str
from services.audit_service import AuditService
from services.monthly_checklist_service import (
    build_records_by_id,
    normalize_doc,
    normalize_text,
    pick_first_value,
    row_to_checklist,
)
from services.sheets_service import GoogleSheetsService
from workflows.monthly_checklist_orchestrator import MonthlyChecklistOrchestrator


@dataclass(frozen=True)
class DatePlan:
    row: ContaOrdemRow
    origin_date: str
    source_date: str
    soma_site_date: str
    soma_sheet_date: str
    payment_ids: list[str]
    status: str
    reason: str = ""


def parse_origin_date(message: Any) -> str:
    match = re.search(r"DATA divergente na origem:\s*origem=([0-9]{2}/[0-9]{2}/[0-9]{4})", str(message or ""))
    return normalize_date_str(match.group(1)) if match else ""


def response_payment_ids(html: str) -> list[str]:
    return re.findall(
        r'<input\s+id="(\d+)"[^>]*class="[^"]*pagamentos_check',
        html,
        re.IGNORECASE,
    )


def load_soma_sheet_by_doc(sheets: GoogleSheetsService) -> dict[str, dict[str, Any]]:
    ws = sheets._sh.worksheet(sheets.settings.sheet_soma)
    values = ws.get_all_values()
    if not values:
        return {}
    headers = [str(header).strip() for header in values[0]]
    out: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(values[1:], start=2):
        record = {
            header: row[idx] if idx < len(row) else ""
            for idx, header in enumerate(headers)
        }
        code = normalize_doc(pick_first_value(record, ("CODIGO", "CÓDIGO")))
        if code:
            record["_ROW"] = row_number
            out[code] = record
    return out


def build_plan(
    row: ContaOrdemRow,
    origin_date: str,
    source_records: dict[str, list[dict[str, Any]]],
    soma_by_doc: dict[str, dict[str, Any]],
    audit: AuditService,
    http: ResilientSession,
    base_url: str,
) -> DatePlan:
    process_key = normalize_text(row.processo)
    source_by_id = source_records.get(process_key, {})
    source_matches = source_by_id.get(row.id_interno, [])
    source_date = ""
    if len(source_matches) == 1:
        source_date = normalize_date_str(
            pick_first_value(source_matches[0], ("DATA VALOR", "DATA MOV.", "DATA", "PAGAMENTO"))
        )

    doc = normalize_doc(row.doc_soma)
    soma = audit.search_by_codigo(doc) if doc else None
    soma_site_date = normalize_date_str(soma.data) if soma else ""
    soma_sheet = soma_by_doc.get(doc, {})
    soma_sheet_date = normalize_date_str(pick_first_value(soma_sheet, ("PAGAMENTO", "DATA")))

    payment_ids: list[str] = []
    if doc and doc.isdigit():
        page = http.get(f"{base_url}/?mod=ivv&exec=entradas_saidas_dados&ID={doc}")
        payment_ids = response_payment_ids(page.text)

    errors: list[str] = []
    if not origin_date:
        errors.append("mensagem ORIGEM sem data de origem")
    if source_date and source_date != origin_date:
        errors.append(f"data lida na origem difere da mensagem: {source_date} != {origin_date}")
    if not source_date:
        errors.append("nao consegui confirmar a data na sheet de origem")
    if not soma:
        errors.append(f"DOC {doc or 'vazio'} nao encontrado no site SOMA")
    else:
        if clean_amount_for_comparison(soma.valor) != clean_amount_for_comparison(row.importancia):
            errors.append(f"valor SOMA difere: {soma.valor} != {row.importancia}")
        if norm_basic(soma.descricao) != norm_basic(row.descricao_soma or row.descricao):
            errors.append(f"descricao SOMA difere: {soma.descricao} != {row.descricao_soma or row.descricao}")
    if not payment_ids:
        errors.append("pagamento atual nao identificado para anular/refazer")
    elif len(payment_ids) != 1:
        errors.append(f"esperado 1 pagamento atual, encontrados {payment_ids}")

    return DatePlan(
        row=row,
        origin_date=origin_date,
        source_date=source_date,
        soma_site_date=soma_site_date,
        soma_sheet_date=soma_sheet_date,
        payment_ids=payment_ids,
        status="BLOQUEADO" if errors else "SIMULADO",
        reason="; ".join(errors),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Simula correcoes para DATA divergente na origem.")
    parser.add_argument("--limit", type=int, default=10, help="Quantidade maxima de casos para simular.")
    parser.add_argument("--all", action="store_true", help="Simula todos os casos.")
    args = parser.parse_args()

    settings = Settings.from_env()
    orch = MonthlyChecklistOrchestrator(settings=settings)
    rows = orch.sheets.get_all_rows(only_entrada_saida=True)
    checklist_rows = [row_to_checklist(row) for row in rows]
    source_records_by_process = orch._load_source_records(checklist_rows)
    source_records = {
        normalize_text(process): build_records_by_id(records)
        for process, records in source_records_by_process.items()
    }
    soma_by_doc = load_soma_sheet_by_doc(orch.sheets)

    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    if not SomaAuthenticator(settings, http).login():
        raise SystemExit("Nao foi possivel autenticar no SOMA.")
    audit = AuditService(settings=settings, http=http, sheets=orch.sheets)
    base_url = settings.site_base_url.rstrip("/")

    targets: list[tuple[ContaOrdemRow, str]] = []
    for row in rows:
        origem_msg = str(row.raw.get("ORIGEM") or "").strip()
        if "DATA divergente na origem" in origem_msg:
            targets.append((row, parse_origin_date(origem_msg)))

    if not args.all:
        targets = targets[: max(args.limit, 0)]

    plans = [
        build_plan(row, origin_date, source_records, soma_by_doc, audit, http, base_url)
        for row, origin_date in targets
    ]

    print("SIMULACAO_DATA_ORIGEM")
    print(f"TOTAL_SIMULADO\t{len(plans)}")
    print("REGRA\tDATA correta = data da origem; atualizar CONTAORDEM, sheet SOMA e site SOMA; anular pagamento e refazer com a nova data.")
    print()

    for plan in plans:
        row = plan.row
        print(f"LINHA\t{row.row_number}")
        print(f"ID_INTERNO\t{row.id_interno}")
        print(f"PROCESSO\t{row.processo}")
        print(f"DOC_SOMA\t{row.doc_soma}")
        print(f"DESCRICAO_SOMA\t{row.descricao_soma or row.descricao}")
        print(f"VALOR\t{row.importancia}")
        print(f"DATA_CONTAORDEM_ATUAL\t{normalize_date_str(row.data_mov)}")
        print(f"DATA_ORIGEM_CORRETA\t{plan.origin_date}")
        print(f"DATA_ORIGEM_CONFIRMADA\t{plan.source_date}")
        print(f"DATA_SITE_SOMA_ATUAL\t{plan.soma_site_date}")
        print(f"DATA_SHEET_SOMA_ATUAL\t{plan.soma_sheet_date}")
        print(f"PAGAMENTOS_A_ANULAR\t{','.join(plan.payment_ids) or 'nenhum'}")
        print("ACAO_1\tAtualizar CONTAORDEM.DATA MOV. para DATA_ORIGEM_CORRETA")
        print("ACAO_2\tAtualizar sheet SOMA.PAGAMENTO/DATA para DATA_ORIGEM_CORRETA")
        print("ACAO_3\tNo site SOMA, alterar data de vencimento/entrada do lancamento para DATA_ORIGEM_CORRETA")
        print("ACAO_4\tAnular baixa e pagamento atual")
        print("ACAO_5\tRefazer pagamento e baixa com DATA_ORIGEM_CORRETA")
        print(f"STATUS\t{plan.status}")
        if plan.reason:
            print(f"MOTIVO\t{plan.reason}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
