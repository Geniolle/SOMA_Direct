import argparse
import re
import sys
import time
from html import unescape
from pathlib import Path
from typing import Any

from gspread.utils import ValueInputOption

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import clean_amount_for_comparison, norm_basic, normalize_date_str
from services.audit_service import AuditService
from services.monthly_checklist_service import normalize_doc, pick_first_value
from services.sheets_service import GoogleSheetsService


def response_status(response) -> int:
    try:
        return int(response.json().get("status", 0))
    except Exception:
        return 0


def selected_value(html: str, select_name: str) -> str:
    pattern = rf'<select\b[^>]*name=["\']{re.escape(select_name)}["\'][^>]*>(.*?)</select>'
    match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    body = match.group(1)
    for option_attrs, _ in re.findall(r"<option\b([^>]*)>(.*?)</option>", body, re.IGNORECASE | re.DOTALL):
        if "selected" in option_attrs.lower():
            value = re.search(r'\bvalue=["\']([^"\']*)', option_attrs, re.IGNORECASE)
            return unescape(value.group(1)) if value else ""
    return ""


def input_value(html: str, name: str, default: str = "") -> str:
    pattern = rf'<input\b[^>]*name=["\']{re.escape(name)}["\'][^>]*>'
    match = re.search(pattern, html, re.IGNORECASE)
    if not match:
        return default
    value = re.search(r'\bvalue=["\']([^"\']*)', match.group(0), re.IGNORECASE)
    return unescape(value.group(1)) if value else default


def payment_ids(html: str) -> list[str]:
    return re.findall(
        r'<input\s+id="(\d+)"[^>]*class="[^"]*pagamentos_check',
        html,
        re.IGNORECASE,
    )


def parse_origin_date(message: Any) -> str:
    match = re.search(r"DATA divergente na origem:\s*origem=([0-9]{2}/[0-9]{2}/[0-9]{4})", str(message or ""))
    return normalize_date_str(match.group(1)) if match else ""


def update_contaordem_date(sheets: GoogleSheetsService, row_number: int, new_date: str) -> None:
    headers = sheets.get_headers()
    indices = {norm_basic(header): idx for idx, header in enumerate(headers)}
    data_idx = indices.get(norm_basic("DATA MOV."))
    if data_idx is None:
        raise RuntimeError("CONTAORDEM precisa da coluna DATA MOV.")
    cell = f"{sheets._col_letter(data_idx + 1)}{row_number}"
    sheets._ws.update(cell, [[new_date]], value_input_option=ValueInputOption.user_entered)


def update_sheet_soma_date(sheets: GoogleSheetsService, doc: str, new_date: str) -> None:
    ws = sheets._sh.worksheet(sheets.settings.sheet_soma)
    values = ws.get_all_values()
    if not values:
        raise RuntimeError("Sheet SOMA vazia")
    headers = [str(header).strip() for header in values[0]]
    indices = {norm_basic(header): idx for idx, header in enumerate(headers)}
    code_idx = indices.get(norm_basic("CODIGO"))
    date_idx = indices.get(norm_basic("PAGAMENTO")) or indices.get(norm_basic("DATA"))
    if code_idx is None or date_idx is None:
        raise RuntimeError("Sheet SOMA precisa das colunas CODIGO e PAGAMENTO/DATA")

    matches: list[int] = []
    for row_number, row in enumerate(values[1:], start=2):
        code = normalize_doc(row[code_idx] if code_idx < len(row) else "")
        if code == doc:
            matches.append(row_number)
    if len(matches) != 1:
        raise RuntimeError(f"DOC {doc} encontrado {len(matches)} vez(es) na sheet SOMA")
    cell = f"{sheets._col_letter(date_idx + 1)}{matches[0]}"
    ws.update(cell, [[new_date]], value_input_option=ValueInputOption.user_entered)


def update_soma_due_date(http: ResilientSession, settings: Settings, doc: str, new_date: str) -> None:
    base_url = settings.site_base_url.rstrip("/")
    page = http.get(f"{base_url}/?mod=ivv&exec=entradas_saidas_dados&ID={doc}")
    if page.status_code != 200:
        raise RuntimeError(f"Falha ao abrir DOC {doc}: HTTP {page.status_code}")
    html = page.text
    payload = {
        "id_fluxo": input_value(html, "id_fluxo", f"{int(doc):010d}"),
        "id_inst": selected_value(html, "id_inst") or settings.institution_id,
        "tipo": "0",
        "tipo_favorecido": "3",
        "id_favorecido": selected_value(html, "id_favorecido"),
        "descricao": input_value(html, "descricao"),
        "id_plano_contas": selected_value(html, "id_plano_contas"),
        "id_centro_custo": selected_value(html, "id_centro_custo"),
        "nf": input_value(html, "nf"),
        "data_vencimento": new_date,
        "data_entrada": new_date,
        "valor": input_value(html, "valor"),
        "descontos": input_value(html, "descontos", "0,00"),
        "multa": input_value(html, "multa", "0,00"),
        "juros": input_value(html, "juros", "0,00"),
        "forma_pagamento": selected_value(html, "forma_pagamento"),
        "parcelas": selected_value(html, "parcelas"),
        "num_cheque": input_value(html, "num_cheque"),
        "num_documento": input_value(html, "num_documento"),
        "id_caixa_origem": selected_value(html, "id_caixa_origem"),
        "obs": input_value(html, "obs"),
        "id_moeda": selected_value(html, "id_moeda") or "2",
        "tipo_pagamento": input_value(html, "tipo_pagamento", "0"),
        "aceitar_caixa_negativo": "1",
        "add": "1",
    }
    required = ("id_fluxo", "id_inst", "descricao", "id_plano_contas", "id_centro_custo", "valor")
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        raise RuntimeError(f"Campos obrigatorios ausentes para atualizar SOMA: {missing}")
    response = http.post(f"{base_url}/?mod=app&exec=entradas_saidas&faz=dados", data=payload)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Falha ao atualizar vencimento no SOMA: HTTP {response.status_code}")
    body = norm_basic(response.text)
    if any(marker in body for marker in ("erro", "falha", "nao foi possivel", "não foi possível")):
        raise RuntimeError(f"SOMA devolveu erro ao atualizar vencimento: {response.text[:300]}")


def delete_payment_and_recreate(
    http: ResilientSession,
    settings: Settings,
    doc: str,
    row: Any,
    new_date: str,
) -> None:
    base_url = settings.site_base_url.rstrip("/")
    page = http.get(f"{base_url}/?mod=ivv&exec=entradas_saidas_dados&ID={doc}")
    ids = payment_ids(page.text)
    if len(ids) != 1:
        raise RuntimeError(f"DOC {doc}: esperado 1 pagamento, encontrados {ids}")
    payment_id = ids[0]
    baixa_response = http.post(f"{base_url}/sys/app/baixas.php", data={"id": payment_id, "excluir": "1"})
    baixa_status = response_status(baixa_response)
    if baixa_status not in (1, 4):
        raise RuntimeError(f"DOC {doc}: falha ao excluir baixa, status={baixa_status}, resp={baixa_response.text[:200]}")
    payment_response = http.post(
        f"{base_url}/sys/app/pagamentos.php",
        data={"id": payment_id, "excluir": "1", "id_f": f"{int(doc):010d}"},
    )
    payment_status = response_status(payment_response)
    if payment_status not in (1, 4):
        raise RuntimeError(f"DOC {doc}: falha ao excluir pagamento, status={payment_status}, resp={payment_response.text[:200]}")

    refreshed = http.get(f"{base_url}/?mod=ivv&exec=entradas_saidas_dados&ID={doc}")
    fluxo_valor = input_value(refreshed.text, "fluxo_valor", clean_amount_for_comparison(row.importancia))
    caixa_options = re.search(
        r'<select\b[^>]*name=["\']id_caixa["\'][^>]*>(.*?)</select>',
        refreshed.text,
        re.IGNORECASE | re.DOTALL,
    )
    id_caixa = ""
    if caixa_options:
        target_caixa = norm_basic(row.caixa or row.caixa_saida)
        for opt_id, opt_text in re.findall(r'<option\b[^>]*value=["\'](\d+)["\'][^>]*>(.*?)</option>', caixa_options.group(1), re.IGNORECASE | re.DOTALL):
            opt_clean = norm_basic(unescape(re.sub(r"<[^>]+>", " ", opt_text)))
            if target_caixa and (target_caixa in opt_clean or opt_clean in target_caixa):
                id_caixa = opt_id
                break
        if not id_caixa:
            first = re.search(r'<option\b[^>]*value=["\'](\d+)["\']', caixa_options.group(1), re.IGNORECASE)
            id_caixa = first.group(1) if first else ""

    target_forma = norm_basic(row.forma_pagamento)
    if "transf" in target_forma:
        forma_val = "3"
    elif "dep" in target_forma:
        forma_val = "1"
    elif "pix" in target_forma:
        forma_val = "4"
    elif "cheq" in target_forma:
        forma_val = "2"
    elif "cart" in target_forma or "maquin" in target_forma:
        forma_val = "5"
    else:
        forma_val = "0"

    if not id_caixa:
        raise RuntimeError(f"DOC {doc}: caixa nao identificado para refazer pagamento")

    payment_payload = {
        "fluxo_desconto": "0,00",
        "fluxo_valor": fluxo_valor,
        "data_pagamento": new_date,
        "forma_pagamento": forma_val,
        "aplicar_desconto": "0",
        "num_cheque": "",
        "num_documento": "",
        "tipo_pagamento": "0",
        "valor_pagamento": fluxo_valor,
        "id_caixa": id_caixa,
        "id_fluxo": f"{int(doc):010d}",
        "add": "1",
        "aceitar_caixa_negativo": "1",
    }
    new_payment = http.post(f"{base_url}/sys/app/pagamentos.php", data=payment_payload)
    try:
        new_payment_json = new_payment.json()
    except Exception:
        new_payment_json = {}
    if new_payment_json.get("status") not in (1, 4, 8) and new_payment_json.get("pago") != 1:
        raise RuntimeError(f"DOC {doc}: falha ao inserir pagamento novo na data {new_date}, resp={new_payment.text[:250]}")

    time.sleep(0.3)
    after_payment = http.get(f"{base_url}/?mod=ivv&exec=entradas_saidas_dados&ID={doc}")
    baixas = re.findall(r'class="[^"]*inst_baixa[^"]*"[^>]*id="(\d+)"', after_payment.text)
    if not baixas:
        baixas = re.findall(r'id="(\d+)"[^>]*class="[^"]*inst_baixa', after_payment.text)
    if not baixas:
        raise RuntimeError(f"DOC {doc}: pagamento novo inserido, mas baixa pendente nao localizada")
    for baixa_id in baixas:
        baixa_payload = {
            "id_pagamento_baixa": baixa_id,
            "data_baixa": new_date,
            "add": "1",
            "aceitar_caixa_negativo": "1",
            "num_doc_baixa": "",
        }
        baixa_new = http.post(f"{base_url}/sys/app/baixas.php", data=baixa_payload)
        baixa_new_status = response_status(baixa_new)
        if baixa_new_status not in (1, 4):
            raise RuntimeError(f"DOC {doc}: falha ao baixar pagamento novo na data {new_date}, status={baixa_new_status}, resp={baixa_new.text[:200]}")


def apply_one_doc(
    settings: Settings,
    sheets: GoogleSheetsService,
    http: ResilientSession,
    audit: AuditService,
    row: Any,
    new_date: str,
) -> None:
    doc = normalize_doc(row.doc_soma)
    before = audit.search_by_codigo(doc)
    if before is None:
        raise RuntimeError(f"DOC {doc} nao encontrado no SOMA")
    if clean_amount_for_comparison(before.valor) != clean_amount_for_comparison(row.importancia):
        raise RuntimeError(f"Valor divergente antes da alteracao: SOMA={before.valor} CONTAORDEM={row.importancia}")
    if norm_basic(before.descricao) != norm_basic(row.descricao_soma or row.descricao):
        raise RuntimeError(f"Descricao divergente antes da alteracao: SOMA={before.descricao} CONTAORDEM={row.descricao_soma or row.descricao}")

    update_soma_due_date(http, settings, doc, new_date)
    time.sleep(0.5)
    delete_payment_and_recreate(http, settings, doc, row, new_date)
    update_contaordem_date(sheets, row.row_number, new_date)
    update_sheet_soma_date(sheets, doc, new_date)

    time.sleep(0.5)
    after = audit.search_by_codigo(doc)
    if after is None:
        raise RuntimeError(f"DOC {doc} nao encontrado no SOMA depois da alteracao")
    if normalize_date_str(after.data) != new_date:
        raise RuntimeError(f"Data final do SOMA nao confere: {after.data} != {new_date}")
    if norm_basic(after.status) != "pago" or norm_basic(after.baixa) != "sim":
        raise RuntimeError(f"Pagamento final nao confirmado: status={after.status}, baixa={after.baixa}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Aplica correcao de DATA divergente para um DOC.")
    parser.add_argument("--doc")
    parser.add_argument("--new-date")
    parser.add_argument("--all", action="store_true", help="Aplica todos os erros DATA divergente na origem.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    if not SomaAuthenticator(settings, http).login():
        raise SystemExit("Nao foi possivel autenticar no SOMA.")
    audit = AuditService(settings=settings, http=http, sheets=sheets)

    all_rows = sheets.get_all_rows(only_entrada_saida=True)
    if args.all:
        targets = []
        for row in all_rows:
            origem = str(row.raw.get("ORIGEM") or "")
            if "DATA divergente na origem" not in origem:
                continue
            new_date = parse_origin_date(origem)
            if new_date:
                targets.append((row, new_date))
        if args.limit and args.limit > 0:
            targets = targets[: args.limit]
    else:
        if not args.doc or not args.new_date:
            raise RuntimeError("Informe --doc/--new-date ou use --all")
        doc = normalize_doc(args.doc)
        targets = [
            (row, normalize_date_str(args.new_date))
            for row in all_rows
            if normalize_doc(row.doc_soma) == doc
        ]
        if len(targets) != 1:
            raise RuntimeError(f"DOC {doc} encontrado {len(targets)} vez(es) na CONTAORDEM")

    print(f"TOTAL_ALVO\t{len(targets)}")
    applied = 0
    blocked = 0
    for row, new_date in targets:
        doc = normalize_doc(row.doc_soma)
        print(f"APLICANDO_DOC\t{doc}\tLINHA\t{row.row_number}\tDATA_ATUAL\t{row.data_mov}\tDATA_NOVA\t{new_date}", flush=True)
        try:
            apply_one_doc(settings, sheets, http, audit, row, new_date)
            applied += 1
            print(f"STATUS\tAPLICADO\tDOC\t{doc}", flush=True)
        except Exception as exc:
            blocked += 1
            print(f"STATUS\tBLOQUEADO\tDOC\t{doc}\tMOTIVO\t{type(exc).__name__}: {exc}", flush=True)
    print(f"APLICADOS\t{applied}")
    print(f"BLOQUEADOS\t{blocked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
