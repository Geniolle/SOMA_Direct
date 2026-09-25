from __future__ import annotations

import argparse
import calendar
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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
logger = logging.getLogger("validate_soma_column")


@dataclass
class ValidationRowResult:
    row_number: int
    id_interno: str
    processo: str
    doc_soma: str
    data_mov: str
    importancia: str
    descricao_soma: str
    tipo: str
    status: str  # Validado, Erro: ..., Vazio
    details: str


def fetch_site_soma_all_months(
    settings: Settings,
    http: ResilientSession,
    audit: AuditService,
    months: Set[Tuple[int, int]],
) -> Dict[str, Any]:
    base_url = settings.site_base_url.rstrip("/") + "/"
    site_records: Dict[str, Any] = {}

    total_months = len(months)
    for idx, (ano, mes) in enumerate(sorted(months), start=1):
        last_day = calendar.monthrange(ano, mes)[1]
        d_start = f"01/{mes:02d}/{ano}"
        d_end = f"{last_day:02d}/{mes:02d}/{ano}"

        for t_d in ("1", "0"):
            payload = {
                "pesquisa": "",
                "filtro": "descricao",
                "id_inst": settings.institution_id,
                "tipo": "2",
                "v": "1",
                "s": "2",
                "t_d": t_d,
                "cc": "-1",
                "c": "",
                "i": d_start,
                "f": d_end,
            }
            try:
                resp = http.post_ajax(f"{base_url}sys/post/buscarEntradasSaidas.php", data=payload)
                for item in audit._parse_search_table(resp.text):
                    code = normalize_document_value(item.codigo)
                    if code and code not in site_records:
                        site_records[code] = item
            except Exception as e:
                logger.warning(f"Erro ao buscar {mes:02d}/{ano} (t_d={t_d}): {e}")

        logger.info(f"[{idx}/{total_months}] SOMA Mês {mes:02d}/{ano} carregado. Total acumulado no site: {len(site_records)}")
        time.sleep(0.1)

    return site_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Validação da coluna SOMA (CONTAORDEM x Site SOMA x Sheet SOMA).")
    parser.add_argument("--apply", action="store_true", help="Grava o resultado na coluna SOMA da CONTAORDEM.")
    parser.add_argument("--dry-run", action="store_true", help="Apenas simula a validação sem gravar.")
    args = parser.parse_args()

    apply = args.apply and not args.dry_run

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    # 1. Carrega CONTAORDEM
    co_values = sheets._ws.get_all_values()
    co_headers = [str(h).strip() for h in co_values[0]]
    indices = {norm_basic(h): i for i, h in enumerate(co_headers)}

    soma_col_idx = indices.get(norm_basic("SOMA"))
    if soma_col_idx is None:
        raise RuntimeError("Coluna 'SOMA' não encontrada na CONTAORDEM")
    soma_col_letter = sheets._col_letter(soma_col_idx + 1)

    dt_idx = indices.get(norm_basic("DATA MOV."))
    val_idx = indices.get(norm_basic("IMPORTÂNCIA"))
    doc_idx = indices.get(norm_basic("DOC. SOMA"))
    desc_soma_idx = indices.get(norm_basic("DESCRIÇÃO SOMA"))
    desc_idx = indices.get(norm_basic("DESCRIÇÃO"))
    tipo_idx = indices.get(norm_basic("TIPO"))
    id_idx = indices.get(norm_basic("ID_INTERNO"))
    proc_idx = indices.get(norm_basic("PROCESSO"))

    # Identifica meses necessários
    months: Set[Tuple[int, int]] = set()
    for row in co_values[1:]:
        doc = row[doc_idx].strip() if doc_idx < len(row) else ""
        if doc.isdigit():
            dt = normalize_date_str(row[dt_idx] if dt_idx < len(row) else "")
            if len(dt) == 10:
                parts = dt.split("/")
                months.add((int(parts[2]), int(parts[1])))

    # 2. Carrega Sheet SOMA
    logger.info("Carregando Sheet SOMA...")
    ws_soma = sheets._sh.worksheet(settings.sheet_soma)
    soma_values = ws_soma.get_all_values()
    soma_headers = [str(h).strip() for h in soma_values[0]]
    soma_indices = {norm_basic(h): i for i, h in enumerate(soma_headers)}

    sh_code_idx = soma_indices.get(norm_basic("CODIGO"))
    sh_tipo_idx = soma_indices.get(norm_basic("TIPO"))
    sh_desc_idx = soma_indices.get(norm_basic("DESCRIÇÃO"))
    sh_val_idx = soma_indices.get(norm_basic("VALOR"))
    sh_dt_idx = soma_indices.get(norm_basic("PAGAMENTO")) or soma_indices.get(norm_basic("DATA"))

    sheet_soma_map: Dict[str, Dict[str, str]] = {}
    for r in soma_values[1:]:
        code = normalize_document_value(r[sh_code_idx] if sh_code_idx < len(r) else "")
        if code:
            sheet_soma_map[code] = {
                "tipo": r[sh_tipo_idx].strip() if sh_tipo_idx < len(r) else "",
                "descricao": r[sh_desc_idx].strip() if sh_desc_idx < len(r) else "",
                "valor": r[sh_val_idx].strip() if sh_val_idx < len(r) else "",
                "data": r[sh_dt_idx].strip() if sh_dt_idx < len(r) else "",
            }
    logger.info(f"Sheet SOMA carregada com {len(sheet_soma_map)} registos.")

    # 3. Autentica e carrega Site SOMA por mês
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    if not auth.login():
        raise SystemExit("Falha ao autenticar no SOMA.")
    audit = AuditService(settings=settings, http=http, sheets=sheets)

    logger.info(f"Carregando {len(months)} meses do Site SOMA...")
    site_soma_map = fetch_site_soma_all_months(settings, http, audit, months)
    logger.info(f"Site SOMA pré-carregado com {len(site_soma_map)} lançamentos.")

    # 4. Executa Validação Linha a Linha
    results: List[ValidationRowResult] = []
    output_cells: List[List[str]] = []

    fallback_lookups = 0
    for r_num, row in enumerate(co_values[1:], start=2):
        doc = normalize_document_value(row[doc_idx] if doc_idx < len(row) else "")
        dt = normalize_date_str(row[dt_idx] if dt_idx < len(row) else "")
        val = clean_amount_for_comparison(row[val_idx] if val_idx < len(row) else "")
        desc = (row[desc_soma_idx] if desc_soma_idx < len(row) else "").strip() or (row[desc_idx] if desc_idx < len(row) else "").strip()
        tipo = (row[tipo_idx] if tipo_idx < len(row) else "").strip()
        id_int = row[id_idx] if id_idx < len(row) else ""
        proc = row[proc_idx] if proc_idx < len(row) else ""

        # Registos sem DOC numérico (transferências vs pendentes)
        if not doc or not doc.isdigit():
            is_transf = (
                "transf" in norm_basic(tipo)
                or "transf" in norm_basic(proc)
                or "cartao" in norm_basic(tipo)
                or "cartao" in norm_basic(proc)
                or "mvv" in norm_basic(tipo)
                or "mvv" in norm_basic(proc)
            )
            if is_transf or not id_int:
                st = ""
                det = "Transferência ou documento não aplicável"
            else:
                st = "esperando atualizar"
                det = "Lançamento pendente de atualização no SOMA"

            results.append(ValidationRowResult(
                row_number=r_num,
                id_interno=id_int,
                processo=proc,
                doc_soma=doc,
                data_mov=dt,
                importancia=val,
                descricao_soma=desc,
                tipo=tipo,
                status=st,
                details=det,
            ))
            output_cells.append([st])
            continue

        errors: List[str] = []

        # --- Validação 1: Site do SOMA ---
        s_site = site_soma_map.get(doc)
        if not s_site:
            # Fallback individual caso não tenha sido retornado no lote do mês
            time.sleep(0.2)
            fallback_lookups += 1
            s_site = audit.search_by_codigo(doc)
            if s_site:
                site_soma_map[doc] = s_site

        if not s_site:
            errors.append("DOC não encontrado no Site SOMA")
        else:
            site_dt = normalize_date_str(s_site.data)
            site_val = clean_amount_for_comparison(s_site.valor)
            site_desc = norm_basic(s_site.descricao)
            site_tipo = norm_basic(s_site.tipo)

            norm_co_desc = norm_basic(desc)
            norm_co_tipo = norm_basic(tipo)

            if site_dt != dt:
                errors.append(f"Site SOMA data: SOMA={site_dt} CONTAORDEM={dt}")
            if site_val != val:
                errors.append(f"Site SOMA valor: SOMA={site_val} CONTAORDEM={val}")
            if site_desc != norm_co_desc:
                errors.append(f"Site SOMA descrição: SOMA='{s_site.descricao}' CONTAORDEM='{desc}'")
            if site_tipo != norm_co_tipo:
                errors.append(f"Site SOMA tipo: SOMA={s_site.tipo} CONTAORDEM={tipo}")

        # --- Validação 2: Sheet SOMA ---
        s_sheet = sheet_soma_map.get(doc)
        if not s_sheet:
            errors.append("DOC não encontrado na Sheet SOMA")
        else:
            sheet_dt = normalize_date_str(s_sheet["data"])
            sheet_val = clean_amount_for_comparison(s_sheet["valor"])
            sheet_desc = norm_basic(s_sheet["descricao"])

            norm_co_desc = norm_basic(desc)

            if sheet_dt != dt:
                errors.append(f"Sheet SOMA data: SOMA={sheet_dt} CONTAORDEM={dt}")
            if sheet_val != val:
                errors.append(f"Sheet SOMA valor: SOMA={sheet_val} CONTAORDEM={val}")
            if sheet_desc != norm_co_desc:
                errors.append(f"Sheet SOMA descrição: SOMA='{s_sheet['descricao']}' CONTAORDEM='{desc}'")

        if errors:
            status_text = "Erro: " + "; ".join(errors)
        else:
            status_text = "Validado"

        results.append(ValidationRowResult(
            row_number=r_num,
            id_interno=id_int,
            processo=proc,
            doc_soma=doc,
            data_mov=dt,
            importancia=val,
            descricao_soma=desc,
            tipo=tipo,
            status=status_text,
            details="OK" if status_text == "Validado" else status_text,
        ))
        output_cells.append([status_text])

    # Estatísticas
    counts = Counter(r.status for r in results)
    total_validados = sum(1 for r in results if r.status == "Validado")
    total_esperando = sum(1 for r in results if r.status == "esperando atualizar")
    total_vazios = sum(1 for r in results if r.status == "")
    total_erros = sum(1 for r in results if r.status.startswith("Erro"))

    print("\n" + "=" * 80)
    print("RELATÓRIO DE VALIDAÇÃO DA COLUNA SOMA")
    print(f"Modo: {'APPLY (Gravando na Folha)' if apply else 'DRY-RUN (Simulação)'}")
    print(f"Total de linhas avaliadas: {len(results)}")
    print(f"Validados: {total_validados}")
    print(f"Esperando atualizar: {total_esperando}")
    print(f"Vazios (Transferências): {total_vazios}")
    print(f"Linhas com Erro: {total_erros}")
    print(f"Consultas individuais de fallback: {fallback_lookups}")
    print("=" * 80)

    if total_erros > 0:
        print("\n--- DETALHAMENTO DAS LINHAS COM ERRO ---")
        for r in results:
            if r.status.startswith("Erro"):
                print(f"Linha {r.row_number} | ID: {r.id_interno} | PROC: {r.processo} | DOC: {r.doc_soma}")
                print(f"  {r.status}")

    # Gravação na Sheet
    if apply:
        cell_range = f"{soma_col_letter}2:{soma_col_letter}{len(co_values)}"
        logger.info(f"Gravando {len(output_cells)} células na coluna {soma_col_letter} ({cell_range})...")
        sheets._ws.update(range_name=cell_range, values=output_cells, value_input_option=ValueInputOption.user_entered)
        logger.info("Coluna SOMA atualizada com sucesso na CONTAORDEM!")
    else:
        print("\n[DRY-RUN] Nenhuma alteração gravada. Use --apply para persistir na folha.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
