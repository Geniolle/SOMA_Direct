from __future__ import annotations

import argparse
import html as html_lib
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gspread.utils import ValueInputOption

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import (
    clean_amount_for_comparison,
    clean_caixa,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
)
from services.sheets_service import GoogleSheetsService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("recreate_soma_payment")


def parse_response_status(resp: Any) -> Optional[int]:
    """Extrai status numérico de respostas JSON ou texto do SOMA."""
    try:
        data = resp.json()
        if isinstance(data, dict) and "status" in data:
            return int(data["status"])
    except Exception:
        pass
    m = re.search(r'"status"\s*:\s*(\d+)', resp.text)
    return int(m.group(1)) if m else None


def payment_ids(html: str) -> List[str]:
    """Busca IDs de pagamentos existentes no formulário do SOMA."""
    ids = re.findall(r'<input\s+id="(\d+)"[^>]*class="[^"]*pagamentos_check', html, re.IGNORECASE)
    if not ids:
        ids = re.findall(r'<button\s+id="(\d+)"[^>]*class="[^"]*exc_baixa', html, re.IGNORECASE)
    return list(dict.fromkeys(ids))


def parse_soma_baixa(html: str) -> Optional[Dict[str, str]]:
    """Extrai informações da tabela de baixas do HTML do documento no SOMA."""
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.DOTALL)
    for r in rows:
        tds = [re.sub(r"<[^>]+>", " ", c).strip() for c in re.findall(r"<td\b[^>]*>(.*?)</td>", r, re.DOTALL)]
        if len(tds) >= 4 and "Registrado" in tds[3]:
            return {
                "data_pagamento": tds[1],
                "data_baixa": tds[2],
                "dados_doc": tds[3],
                "valor": tds[4] if len(tds) > 4 else "",
            }
    return None


def parse_caixa_forma(dados_doc: str) -> Tuple[str, str]:
    """Extrai Caixa e Forma de Pagamento da string DADOS DOC."""
    parts = [p.strip() for p in dados_doc.split(",") if p.strip()]
    if len(parts) < 3:
        return "", ""
    caixa = parts[1]
    resto = parts[2]
    forma = re.split(r"(\.|\bN[º°]|\bbaixa\b)", resto, flags=re.IGNORECASE)[0].strip()
    return caixa, forma


def get_forma_pagamento_code(forma_name: str) -> str:
    """Mapeia o nome da forma de pagamento para o código oficial do SOMA."""
    f = norm_basic(forma_name)
    if "dinheiro" in f or "numerario" in f or "especie" in f:
        return "0"
    elif "deposito" in f or "dep" in f:
        return "1"
    elif "cheque" in f or "cheq" in f:
        return "2"
    elif "transf" in f:
        return "3"
    elif "pix" in f:
        return "4"
    elif "cartao" in f or "maquineta" in f or "tpa" in f:
        return "5"
    return "0"


def resolve_caixa_id(html: str, target_caixa_name: str) -> Optional[str]:
    """Resolve o ID do caixa a partir do <select name='id_caixa'> na página do SOMA."""
    caixa_select = re.search(r'<select\b[^>]*name=["\']id_caixa["\'][^>]*>(.*?)</select>', html, re.IGNORECASE | re.DOTALL)
    if not caixa_select:
        return None

    target_norm = clean_caixa(target_caixa_name)
    options = re.findall(r'<option\b[^>]*value=["\'](\d+)["\'][^>]*>(.*?)</option>', caixa_select.group(1), re.IGNORECASE | re.DOTALL)
    for opt_id, opt_text in options:
        opt_clean = clean_caixa(html_lib.unescape(re.sub(r"<[^>]+>", " ", opt_text)))
        if target_norm and (target_norm == opt_clean or target_norm in opt_clean or opt_clean in target_norm):
            return opt_id
    return None


def correct_single_doc_payment(
    http: ResilientSession,
    settings: Settings,
    sheets: GoogleSheetsService,
    doc: str,
    row_number: int,
    apply: bool = False,
) -> Dict[str, Any]:
    """Corrige o pagamento no portal SOMA, alinhando com Caixa, Forma e Data da CONTAORDEM."""
    base_url = settings.site_base_url.rstrip("/")
    ws_co = sheets._sh.worksheet(settings.sheet_contaordem)
    row_vals = ws_co.row_values(row_number)
    headers = ws_co.row_values(1)
    indices = {norm_basic(h): i for i, h in enumerate(headers)}

    doc_in_sheet = row_vals[indices[norm_basic("DOC. SOMA")]].strip()
    if doc_in_sheet != doc:
        raise ValueError(f"Linha {row_number} possui DOC '{doc_in_sheet}', diferente do esperado '{doc}'")

    data_mov = normalize_date_str(row_vals[indices[norm_basic("DATA MOV.")]].strip())
    caixa_co = row_vals[indices[norm_basic("CAIXA")]].strip()
    forma_co = row_vals[indices[norm_basic("FORMA DE PAGAMENTO")]].strip()
    valor_co = clean_amount_for_comparison(row_vals[indices[norm_basic("IMPORTÂNCIA")]].strip())

    logger.info(f"Linha {row_number} | DOC {doc}:")
    logger.info(f"  CONTAORDEM: Data={data_mov}, Caixa='{caixa_co}', Forma='{forma_co}', Valor={valor_co} €")

    # 1. Carrega dados atuais no portal SOMA
    url_doc = f"{base_url}/?mod=ivv&exec=entradas_saidas_dados&ID={doc}"
    page = http.get(url_doc)
    if page.status_code != 200:
        raise RuntimeError(f"Falha ao carregar DOC {doc} no SOMA: HTTP {page.status_code}")

    html_before = page.text
    existing_baixa = parse_soma_baixa(html_before)
    logger.info(f"  SOMA ANTES: {existing_baixa['dados_doc'] if existing_baixa else 'Sem baixa'}")

    ids = payment_ids(html_before)
    logger.info(f"  IDs de pagamento encontrados: {ids}")
    if len(ids) != 1:
        raise RuntimeError(f"DOC {doc}: esperado exatamente 1 pagamento ativo, encontrados {len(ids)}: {ids}")

    payment_id = ids[0]
    forma_code = get_forma_pagamento_code(forma_co)
    caixa_id = resolve_caixa_id(html_before, caixa_co)
    if not caixa_id:
        raise RuntimeError(f"Não foi possível localizar o caixa '{caixa_co}' no formulário do SOMA para o DOC {doc}")

    logger.info(f"  Parâmetros para novo pagamento: Data={data_mov}, Forma={forma_code} ('{forma_co}'), Caixa ID={caixa_id} ('{caixa_co}')")

    if not apply:
        logger.info("[SIMULAÇÃO] Nenhuma alteração persistida no SOMA nem na sheet.")
        return {
            "status": "SIMULADO",
            "doc": doc,
            "row_number": row_number,
            "payment_id": payment_id,
            "data_mov": data_mov,
            "caixa_co": caixa_co,
            "caixa_id": caixa_id,
            "forma_co": forma_co,
            "forma_code": forma_code,
            "valor_co": valor_co,
        }

    # 2. Cancela Baixa
    logger.info(f"Cancelando baixa atual (ID {payment_id})...")
    resp_baixa = http.post(f"{base_url}/sys/app/baixas.php", data={"id": payment_id, "excluir": "1"})
    st_baixa = parse_response_status(resp_baixa)
    if st_baixa not in (1, 4):
        raise RuntimeError(f"Falha ao cancelar baixa {payment_id}: status={st_baixa}, body={resp_baixa.text[:200]}")
    logger.info(f"Baixa {payment_id} cancelada com sucesso (status {st_baixa}).")

    # 3. Cancela Pagamento
    logger.info(f"Cancelando pagamento atual (ID {payment_id})...")
    resp_pag = http.post(
        f"{base_url}/sys/app/pagamentos.php",
        data={"id": payment_id, "excluir": "1", "id_f": f"{int(doc):010d}"},
    )
    st_pag = parse_response_status(resp_pag)
    if st_pag not in (1, 4):
        raise RuntimeError(f"Falha ao cancelar pagamento {payment_id}: status={st_pag}, body={resp_pag.text[:200]}")
    logger.info(f"Pagamento {payment_id} cancelado com sucesso (status {st_pag}).")

    time.sleep(1)

    # 4. Insere Novo Pagamento com parâmetros corretos da CONTAORDEM
    logger.info(f"Inserindo novo pagamento no SOMA para DOC {doc}...")
    payment_payload = {
        "fluxo_desconto": "0,00",
        "fluxo_valor": valor_co,
        "data_pagamento": data_mov,
        "forma_pagamento": forma_code,
        "aplicar_desconto": "0",
        "num_cheque": "",
        "num_documento": "",
        "tipo_pagamento": "0",
        "valor_pagamento": valor_co,
        "id_caixa": caixa_id,
        "id_fluxo": f"{int(doc):010d}",
        "add": "1",
        "aceitar_caixa_negativo": "1",
    }
    resp_new = http.post(f"{base_url}/sys/app/pagamentos.php", data=payment_payload)
    st_new = parse_response_status(resp_new)
    if st_new != 1:
        raise RuntimeError(f"Falha ao inserir novo pagamento no SOMA: status={st_new}, body={resp_new.text[:300]}")
    logger.info(f"Novo pagamento inserido no SOMA com sucesso (status {st_new})!")

    time.sleep(1)

    # 5. Revalida no SOMA e extrai novo DADOS DOC
    page_after = http.get(url_doc)
    new_baixa = parse_soma_baixa(page_after.text)
    if not new_baixa:
        raise RuntimeError(f"DOC {doc}: pagamento inserido mas nenhuma baixa foi retornada após atualização!")

    new_dados_doc = new_baixa["dados_doc"]
    new_data_pag = new_baixa["data_pagamento"]
    s_caixa, s_forma = parse_caixa_forma(new_dados_doc)
    logger.info(f"  SOMA DEPOIS: {new_dados_doc}")
    logger.info(f"  SOMA DEPOIS Parsed: Data={new_data_pag}, Caixa='{s_caixa}', Forma='{s_forma}'")

    # 6. Atualiza Sheet CONTAORDEM (DADOS DOC e CHECK)
    dados_col_letter = sheets._col_letter(indices[norm_basic("DADOS DOC")] + 1)
    check_col_letter = sheets._col_letter(indices[norm_basic("CHECK")] + 1)

    ws_co.update_acell(f"{dados_col_letter}{row_number}", new_dados_doc)
    ws_co.update_acell(f"{check_col_letter}{row_number}", "Validado")
    logger.info(f"CONTAORDEM Linha {row_number}: DADOS DOC atualizado e CHECK gravado como 'Validado'!")

    return {
        "status": "APLICADO",
        "doc": doc,
        "row_number": row_number,
        "data_mov": data_mov,
        "caixa_co": caixa_co,
        "forma_co": forma_co,
        "dados_doc_anterior": existing_baixa["dados_doc"] if existing_baixa else "",
        "dados_doc_novo": new_dados_doc,
        "check_status": "Validado",
    }


def run_batch_corrections(
    http: ResilientSession,
    settings: Settings,
    sheets: GoogleSheetsService,
    apply: bool = False,
) -> None:
    """Executa a correção em lote para todas as linhas com erro na coluna CHECK."""
    ws = sheets._sh.worksheet(settings.sheet_contaordem)
    values = ws.get_all_values()
    headers = values[0]
    indices = {norm_basic(h): i for i, h in enumerate(headers)}

    check_idx = indices.get(norm_basic("CHECK"))
    doc_idx = indices.get(norm_basic("DOC. SOMA"))

    targets: List[Tuple[int, str]] = []
    for r_num, row in enumerate(values[1:], start=2):
        c = row[check_idx].strip() if check_idx < len(row) else ""
        doc = row[doc_idx].strip() if doc_idx < len(row) else ""
        if c.startswith("Erro") and doc.isdigit():
            targets.append((r_num, doc))

    total = len(targets)
    logger.info(f"Encontradas {total} linhas com erro na coluna CHECK para correção em lote.")
    print("=" * 80)
    print(f"CORREÇÃO EM LOTE DA COLUNA CHECK ({total} ALVOS)")
    print(f"Modo: {'APPLY (Real)' if apply else 'DRY-RUN (Simulação)'}")
    print("=" * 80)

    applied = 0
    failed = 0

    for idx, (r_num, doc) in enumerate(targets, start=1):
        print(f"\n[{idx}/{total}] Processando Linha {r_num} | DOC {doc}...")
        try:
            res = correct_single_doc_payment(
                http=http,
                settings=settings,
                sheets=sheets,
                doc=doc,
                row_number=r_num,
                apply=apply,
            )
            applied += 1
            print(f"[{idx}/{total}] SUCESSO: Linha {r_num} (DOC {doc}) -> {res.get('check_status', 'OK')}")
        except Exception as e:
            failed += 1
            logger.error(f"[{idx}/{total}] FALHA na Linha {r_num} (DOC {doc}): {e}")
            print(f"[{idx}/{total}] ERRO: Linha {r_num} (DOC {doc}): {e}")

        time.sleep(0.5)

    print("\n" + "=" * 80)
    print("RESUMO DO LOTE:")
    print(f"Total de alvos: {total}")
    print(f"Sucesso: {applied}")
    print(f"Falhas: {failed}")
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Corrige baixa/pagamento no SOMA com base na CONTAORDEM.")
    parser.add_argument("--doc", type=str, default="", help="DOC. SOMA do registro.")
    parser.add_argument("--row", type=int, default=0, help="Número da linha na CONTAORDEM.")
    parser.add_argument("--batch", action="store_true", help="Executa correção em lote para todos os erros da coluna CHECK.")
    parser.add_argument("--apply", action="store_true", help="Aplica a alteração real no portal SOMA e na folha.")
    args = parser.parse_args()

    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    if not auth.login():
        raise SystemExit("Falha ao autenticar no portal SOMA.")

    sheets = GoogleSheetsService(settings)

    if args.batch:
        run_batch_corrections(http=http, settings=settings, sheets=sheets, apply=args.apply)
    else:
        if not args.doc or not args.row:
            parser.error("Para execução individual informe --doc e --row, ou use --batch.")

        print("=" * 80)
        print("CORREÇÃO DE PAGAMENTO NO SOMA (INDIVIDUAL)")
        print(f"Modo: {'APPLY (Real)' if args.apply else 'DRY-RUN (Simulação)'}")
        print(f"Linha: {args.row} | DOC: {args.doc}")
        print("=" * 80)

        res = correct_single_doc_payment(
            http=http,
            settings=settings,
            sheets=sheets,
            doc=args.doc,
            row_number=args.row,
            apply=args.apply,
        )

        print("\n" + "=" * 80)
        print("RESULTADO DO PROCESSAMENTO:")
        for k, v in res.items():
            print(f"  {k}: {v}")
        print("=" * 80)


if __name__ == "__main__":
    main()

