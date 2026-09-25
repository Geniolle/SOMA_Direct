from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from gspread.utils import ValueInputOption

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import (
    clean_amount_for_comparison,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
)
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("apply_all_remaining_corrections")


def main() -> int:
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    ws_co = sheets._ws
    ws_soma = sheets._sh.worksheet(settings.sheet_soma)

    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    if not auth.login():
        raise SystemExit("Falha ao autenticar no SOMA.")
    audit = AuditService(settings=settings, http=http, sheets=sheets)

    print("=" * 80)
    print("APLICAÇÃO DE CORREÇÃO NOS 58 REGISTOS RESTANTES")
    print("=" * 80)

    # 1. Carrega CONTAORDEM
    co_vals = ws_co.get_all_values()
    co_headers = [str(h).strip() for h in co_vals[0]]
    indices = {norm_basic(h): i for i, h in enumerate(co_headers)}

    doc_col_letter = sheets._col_letter(indices[norm_basic("DOC. SOMA")] + 1)
    desc_soma_col_letter = sheets._col_letter(indices[norm_basic("DESCRIÇÃO SOMA")] + 1)
    soma_col_letter = sheets._col_letter(indices[norm_basic("SOMA")] + 1)

    # 2. Carrega Sheet SOMA
    soma_vals = ws_soma.get_all_values()
    soma_headers = [str(h).strip() for h in soma_vals[0]]
    soma_indices = {norm_basic(h): i for i, h in enumerate(soma_headers)}
    code_idx = soma_indices.get(norm_basic("CODIGO"))
    soma_desc_col_letter = sheets._col_letter(soma_indices.get(norm_basic("DESCRIÇÃO")) + 1)

    soma_doc_to_row = {}
    for r_num, row in enumerate(soma_vals[1:], start=2):
        code = normalize_document_value(row[code_idx] if code_idx < len(row) else "")
        if code:
            soma_doc_to_row[code] = r_num

    co_updates: List[Dict[str, Any]] = []
    sheet_soma_updates: List[Dict[str, Any]] = []

    # ----------------------------------------------------
    # A. Corrigir Linhas 203 e 204 (VC_VENDAS)
    # ----------------------------------------------------
    print("\n--- A. CORRIGINDO REGISTOS VC_VENDAS (LINHAS 203 e 204) ---")
    for r_num in (203, 204):
        row = co_vals[r_num - 1]
        id_int = row[indices[norm_basic("ID_INTERNO")]]
        dt = row[indices[norm_basic("DATA MOV.")]]
        val = row[indices[norm_basic("IMPORTÂNCIA")]]
        curr_doc = row[indices[norm_basic("DOC. SOMA")]]
        curr_desc = row[indices[norm_basic("DESCRIÇÃO SOMA")]]

        print(f"Linha {r_num} | ID: {id_int} | DATA: {dt} | VALOR: {val} €")
        print(f"  DOC. SOMA: {curr_doc} -> LIMPO ('')")
        print(f"  DESCRIÇÃO SOMA: Mantido '{curr_desc}' (sequencial único correto)")
        print(f"  Coluna SOMA: 'esperando atualizar'")

        co_updates.append({"range": f"{doc_col_letter}{r_num}", "values": [[""]]})
        co_updates.append({"range": f"{soma_col_letter}{r_num}", "values": [["esperando atualizar"]]})

    # ----------------------------------------------------
    # B. Corrigir as 56 Linhas de DÍZIMOS/OFERTAS
    # ----------------------------------------------------
    print("\n--- B. CORRIGINDO 56 REGISTOS DE DÍZIMOS/OFERTAS ---")
    diz_count = 0
    for r_num, row in enumerate(co_vals[1:], start=2):
        status = row[indices[norm_basic("SOMA")]] if norm_basic("SOMA") in indices else ""
        proc = row[indices[norm_basic("PROCESSO")]]
        doc = normalize_document_value(row[indices[norm_basic("DOC. SOMA")]])

        if not (status.startswith("Erro") and "DÍZIMOS" in proc):
            continue

        id_int = row[indices[norm_basic("ID_INTERNO")]]
        dt = row[indices[norm_basic("DATA MOV.")]]
        val = row[indices[norm_basic("IMPORTÂNCIA")]]
        curr_desc = row[indices[norm_basic("DESCRIÇÃO SOMA")]]

        # Busca no SOMA
        time.sleep(0.15)
        item = audit.search_by_codigo(doc)
        if not item:
            logger.error(f"DOC {doc} não encontrado no SOMA para linha {r_num}!")
            continue

        soma_desc = item.descricao.strip()
        print(f"Linha {r_num} | ID: {id_int} | DOC: {doc} | DATA: {dt} | VALOR: {val} €")
        print(f"  DESCRIÇÃO SOMA: '{curr_desc}' -> '{soma_desc}'")
        print(f"  Coluna SOMA: 'Validado'")

        # Atualização CONTAORDEM
        co_updates.append({"range": f"{desc_soma_col_letter}{r_num}", "values": [[soma_desc]]})
        co_updates.append({"range": f"{soma_col_letter}{r_num}", "values": [["Validado"]]})

        # Atualização Sheet SOMA
        if doc in soma_doc_to_row:
            s_row = soma_doc_to_row[doc]
            sheet_soma_updates.append({"range": f"{soma_desc_col_letter}{s_row}", "values": [[soma_desc]]})

        diz_count += 1

    print(f"\nTotal de registos DÍZIMOS/OFERTAS preparados: {diz_count}")

    # 3. Grava em Batch na CONTAORDEM
    print(f"\n-> Gravando {len(co_updates)} células na folha CONTAORDEM...")
    ws_co.batch_update(co_updates, value_input_option=ValueInputOption.user_entered)
    print("-> Folha CONTAORDEM atualizada com sucesso.")

    # 4. Grava em Batch na Sheet SOMA
    if sheet_soma_updates:
        print(f"-> Gravando {len(sheet_soma_updates)} células na Sheet SOMA...")
        ws_soma.batch_update(sheet_soma_updates, value_input_option=ValueInputOption.user_entered)
        print("-> Sheet SOMA atualizada com sucesso.")

    print("\n" + "=" * 80)
    print("APLICAÇÃO CONCLUÍDA COM SUCESSO!")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
