from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
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
    clean_caixa,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
)
from services.sheets_service import GoogleSheetsService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("validate_check_column")


@dataclass
class CheckRowResult:
    row_number: int
    id_interno: str
    processo: str
    doc_soma: str
    data_mov: str
    caixa: str
    forma_pagamento: str
    dados_doc: str
    dados_doc_updated: bool
    check_status: str
    details: str


def extract_status_from_dados_doc(dados_doc: str) -> str:
    """Extrai o status (ex: 'Registrado', 'Não conferido') da string DADOS DOC."""
    if "Não conferido" in dados_doc:
        return "Não conferido"
    elif "Registrado" in dados_doc:
        return "Registrado"
    return "Desconhecido"


def parse_soma_baixa(html: str) -> Optional[Dict[str, str]]:
    """Extrai informações da tabela de baixas do HTML do documento no SOMA."""
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.DOTALL)
    for r in rows:
        tds = [re.sub(r"<[^>]+>", " ", c).strip() for c in re.findall(r"<td\b[^>]*>(.*?)</td>", r, re.DOTALL)]
        if len(tds) >= 4 and ("Registrado" in tds[3] or "Não conferido" in tds[3]):
            status = extract_status_from_dados_doc(tds[3])
            return {
                "data_pagamento": tds[1],
                "data_baixa": tds[2],
                "dados_doc": tds[3],
                "status": status,
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


def validate_check_coherence(
    data_mov: str,
    sheet_caixa: str,
    sheet_forma: str,
    soma_data: str,
    soma_caixa: str,
    soma_forma: str,
) -> Tuple[bool, List[str]]:
    """Valida a coerência entre CONTAORDEM e os dados registrados no SOMA."""
    errors: List[str] = []

    # 1. Validação de Data
    norm_mov = normalize_date_str(data_mov)
    norm_soma = normalize_date_str(soma_data)
    if norm_mov and norm_soma and norm_mov != norm_soma:
        errors.append(f"DATA divergente: SOMA={norm_soma} CONTAORDEM={norm_mov}")

    # 2. Validação de Caixa
    c_sheet = clean_caixa(sheet_caixa)
    c_soma = clean_caixa(soma_caixa)
    if c_sheet and c_soma and c_sheet != c_soma and c_sheet not in c_soma and c_soma not in c_sheet:
        errors.append(f"CAIXA divergente: SOMA='{soma_caixa}' CONTAORDEM='{sheet_caixa}'")

    # 3. Validação de Forma de Pagamento
    f_sheet = norm_basic(sheet_forma)
    f_soma = norm_basic(soma_forma)
    transf_syn = {"transferencia bancaria", "transferencia", "deposito"}
    cash_syn = {"dinheiro", "numerario", "especie"}

    if f_sheet and f_soma:
        if f_sheet != f_soma:
            if f_sheet in transf_syn and f_soma in transf_syn:
                pass
            elif f_sheet in cash_syn and f_soma in cash_syn:
                pass
            elif f_sheet in f_soma or f_soma in f_sheet:
                pass
            else:
                errors.append(f"FORMA divergente: SOMA='{soma_forma}' CONTAORDEM='{sheet_forma}'")

    return (len(errors) == 0, errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validação da coluna CHECK e sincronização de DADOS DOC.")
    parser.add_argument("--apply", action="store_true", help="Persiste as alterações nas colunas DADOS DOC e CHECK.")
    parser.add_argument("--dry-run", action="store_true", help="Apenas simula sem gravar.")
    parser.add_argument("--pilot", type=str, default="", help="Executa apenas para um DOC. SOMA específico.")
    parser.add_argument("--refresh-all", action="store_true", help="Reconsulta todos os DOCs no site do SOMA.")
    parser.add_argument("--limit", type=int, default=0, help="Limita o processamento a N registros.")
    args = parser.parse_args()

    apply = args.apply and not args.dry_run

    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    if not auth.login():
        raise SystemExit("Falha ao autenticar no portal SOMA.")

    sheets = GoogleSheetsService(settings)
    co_values = sheets._ws.get_all_values()
    co_headers = [str(h).strip() for h in co_values[0]]
    indices = {norm_basic(h): i for i, h in enumerate(co_headers)}

    check_col_idx = indices.get(norm_basic("CHECK"))
    dados_col_idx = indices.get(norm_basic("DADOS DOC"))
    doc_idx = indices.get(norm_basic("DOC. SOMA"))
    dt_idx = indices.get(norm_basic("DATA MOV."))
    caixa_idx = indices.get(norm_basic("CAIXA"))
    forma_idx = indices.get(norm_basic("FORMA DE PAGAMENTO"))
    tipo_idx = indices.get(norm_basic("TIPO"))
    proc_idx = indices.get(norm_basic("PROCESSO"))
    id_idx = indices.get(norm_basic("ID_INTERNO"))

    if check_col_idx is None or dados_col_idx is None:
        raise RuntimeError("Colunas 'CHECK' ou 'DADOS DOC' não encontradas na CONTAORDEM")

    check_col_letter = sheets._col_letter(check_col_idx + 1)
    dados_col_letter = sheets._col_letter(dados_col_idx + 1)
    base_url = settings.site_base_url.rstrip("/")

    results: List[CheckRowResult] = []
    dados_cells: List[List[str]] = []
    check_cells: List[List[str]] = []

    fetched_count = 0
    updated_dados_count = 0

    total_rows = len(co_values) - 1
    logger.info(f"Iniciando validação de {total_rows} linhas...")

    for r_num, row in enumerate(co_values[1:], start=2):
        doc = normalize_document_value(row[doc_idx] if doc_idx < len(row) else "")
        dt = normalize_date_str(row[dt_idx] if dt_idx < len(row) else "")
        caixa = (row[caixa_idx] if caixa_idx < len(row) else "").strip()
        forma = (row[forma_idx] if forma_idx < len(row) else "").strip()
        dados = (row[dados_col_idx] if dados_col_idx < len(row) else "").strip()
        tipo = (row[tipo_idx] if tipo_idx < len(row) else "").strip()
        proc = (row[proc_idx] if proc_idx < len(row) else "").strip()
        id_int = (row[id_idx] if id_idx < len(row) else "").strip()

        if args.pilot and doc != args.pilot:
            check_cells.append([row[check_col_idx] if check_col_idx < len(row) else ""])
            dados_cells.append([dados])
            continue

        if args.limit and len(results) >= args.limit:
            break

        # Caso 1: Movimento sem DOC numérico (Transferências, etc.)
        if not doc or not doc.isdigit():
            results.append(CheckRowResult(
                row_number=r_num,
                id_interno=id_int,
                processo=proc,
                doc_soma=doc,
                data_mov=dt,
                caixa=caixa,
                forma_pagamento=forma,
                dados_doc=dados,
                dados_doc_updated=False,
                check_status="",
                details="Transferência ou documento não aplicável",
            ))
            check_cells.append([""])
            dados_cells.append([dados])
            continue

        # Caso 2: Movimento com DOC numérico
        # Determina se precisamos consultar o portal SOMA
        needs_fetch = (
            args.refresh_all
            or not dados
            or not dados.startswith("Registrado")
            or (" às " not in dados and " s " not in dados)
        )

        dados_doc_final = dados
        dados_updated = False
        soma_dt = dt
        soma_caixa = ""
        soma_forma = ""

        soma_status = ""
        if needs_fetch:
            time.sleep(0.3)
            fetched_count += 1
            url = f"{base_url}/?mod=ivv&exec=entradas_saidas_dados&ID={doc}"
            try:
                resp = http.get(url)
                b_info = parse_soma_baixa(resp.text)
                if b_info:
                    dados_doc_final = b_info["dados_doc"]
                    soma_dt = b_info["data_pagamento"]
                    soma_status = b_info.get("status", "")
                    soma_caixa, soma_forma = parse_caixa_forma(dados_doc_final)
                    if dados_doc_final != dados:
                        dados_updated = True
                        updated_dados_count += 1
                else:
                    dados_doc_final = dados or "Lançamento em aberto no SOMA (sem baixa/pagamento)"
                    soma_caixa, soma_forma = "", ""
            except Exception as e:
                logger.warning(f"Erro ao buscar DOC {doc}: {e}")
                dados_doc_final = dados
        else:
            soma_caixa, soma_forma = parse_caixa_forma(dados)
            if dados:
                soma_status = extract_status_from_dados_doc(dados)

        # Validação de Coerência
        if soma_status == "Não conferido":
            check_status = "Não conferido"
        elif not soma_caixa and not soma_forma:
            check_status = "Erro: Baixa não encontrada no SOMA"
        else:
            ok, errors = validate_check_coherence(
                data_mov=dt,
                sheet_caixa=caixa,
                sheet_forma=forma,
                soma_data=soma_dt,
                soma_caixa=soma_caixa,
                soma_forma=soma_forma,
            )
            if ok:
                check_status = "Validado"
            else:
                check_status = "Erro: " + "; ".join(errors)

        results.append(CheckRowResult(
            row_number=r_num,
            id_interno=id_int,
            processo=proc,
            doc_soma=doc,
            data_mov=dt,
            caixa=caixa,
            forma_pagamento=forma,
            dados_doc=dados_doc_final,
            dados_doc_updated=dados_updated,
            check_status=check_status,
            details="OK" if check_status == "Validado" else check_status,
        ))
        check_cells.append([check_status])
        dados_cells.append([dados_doc_final])

    # Estatísticas
    total_validados = sum(1 for r in results if r.check_status == "Validado")
    total_vazios = sum(1 for r in results if r.check_status == "")
    total_nao_conferidos = sum(1 for r in results if r.check_status == "Não conferido")
    total_erros = sum(1 for r in results if r.check_status.startswith("Erro"))

    print("\n" + "=" * 80)
    print("RELATÓRIO DE VALIDAÇÃO DA COLUNA CHECK E DADOS DOC")
    print(f"Modo: {'APPLY (Gravando na Folha)' if apply else 'DRY-RUN (Simulação)'}")
    print(f"Total de linhas avaliadas: {len(results)}")
    print(f"Validados: {total_validados}")
    print(f"Não conferidos (status SOMA): {total_nao_conferidos}")
    print(f"Vazios (Transferências): {total_vazios}")
    print(f"Linhas com Erro: {total_erros}")
    print(f"Consultas realizadas no portal SOMA: {fetched_count}")
    print(f"DADOS DOC atualizados com dados oficiais do SOMA: {updated_dados_count}")
    print("=" * 80)

    if total_erros > 0 or total_nao_conferidos > 0:
        print("\n--- DETALHAMENTO DAS LINHAS COM ERRO OU NÃO CONFERIDAS ---")
        for r in results:
            if r.check_status.startswith("Erro") or r.check_status == "Não conferido":
                print(f"Linha {r.row_number} | ID: {r.id_interno} | PROC: {r.processo} | DOC: {r.doc_soma}")
                print(f"  CONTAORDEM: Data={r.data_mov}, Caixa='{r.caixa}', Forma='{r.forma_pagamento}'")
                print(f"  DADOS DOC:  {r.dados_doc}")
                print(f"  STATUS:     {r.check_status}")

    # Gravação na Sheet
    if apply:
        # 1. Grava DADOS DOC (coluna V)
        dados_range = f"{dados_col_letter}2:{dados_col_letter}{len(co_values)}"
        logger.info(f"Gravando {len(dados_cells)} células na coluna {dados_col_letter} ({dados_range})...")
        sheets._ws.update(range_name=dados_range, values=dados_cells, value_input_option=ValueInputOption.user_entered)

        # 2. Grava CHECK (coluna W)
        check_range = f"{check_col_letter}2:{check_col_letter}{len(co_values)}"
        logger.info(f"Gravando {len(check_cells)} células na coluna {check_col_letter} ({check_range})...")
        sheets._ws.update(range_name=check_range, values=check_cells, value_input_option=ValueInputOption.user_entered)

        logger.info("Colunas DADOS DOC e CHECK atualizadas com sucesso na CONTAORDEM!")
    else:
        print("\n[DRY-RUN] Nenhuma alteração gravada. Use --apply para persistir na folha.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
