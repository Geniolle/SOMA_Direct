from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections import defaultdict
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
    ContaOrdemRow,
    extract_suffix_n,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
    strip_date_prefix,
    strip_suffix_n,
)
from services.audit_service import AuditService
from services.reconciliation_resolver import ReconciliationResolver
from services.sheets_service import GoogleSheetsService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("apply_resolve_duplicates")


def similar_base(left: Any, right: Any) -> bool:
    left_norm = norm_basic(strip_date_prefix(strip_suffix_n(left)))
    right_norm = norm_basic(strip_date_prefix(strip_suffix_n(right)))
    if not left_norm or not right_norm:
        return False
    return left_norm == right_norm or left_norm in right_norm or right_norm in left_norm


@dataclass
class DuplicatePlan:
    row_number: int
    data_mov: str
    id_interno: str
    processo: str
    doc_soma: str
    current_desc: str
    proposed_desc: str
    soma_date: str
    soma_desc: str
    action: str  # MANTER, ATUALIZAR, BLOQUEADO
    reason: str


class SheetBatchUpdater:
    def __init__(self, sheets: GoogleSheetsService):
        self.sheets = sheets
        self.ws_contaordem = sheets._ws
        self.ws_soma = sheets._sh.worksheet(sheets.settings.sheet_soma)

        # Cache headers e colunas de CONTAORDEM
        co_headers = self.ws_contaordem.row_values(1)
        co_indices = {norm_basic(h): i for i, h in enumerate(co_headers)}
        self.co_desc_col_letter = sheets._col_letter(co_indices[norm_basic("DESCRIÇÃO SOMA")] + 1)

        # Cache doc -> row de sheet SOMA
        soma_values = self.ws_soma.get_all_values()
        soma_headers = [str(h).strip() for h in soma_values[0]]
        soma_indices = {norm_basic(h): i for i, h in enumerate(soma_headers)}
        code_idx = soma_indices.get(norm_basic("CODIGO"))
        desc_idx = soma_indices.get(norm_basic("DESCRIÇÃO"))
        if code_idx is None or desc_idx is None:
            raise RuntimeError("Sheet SOMA precisa das colunas CODIGO e DESCRIÇÃO")

        self.soma_desc_col_letter = sheets._col_letter(desc_idx + 1)
        self.soma_doc_to_rows: Dict[str, List[int]] = defaultdict(list)
        for r_num, row in enumerate(soma_values[1:], start=2):
            code = normalize_document_value(row[code_idx] if code_idx < len(row) else "")
            if code:
                self.soma_doc_to_rows[code].append(r_num)

    def apply_batch(self, updates: List[Tuple[int, str, str]]) -> None:
        """
        updates: Lista de (row_number_contaordem, doc_soma, new_desc)
        """
        if not updates:
            return

        for attempt in range(1, 4):
            try:
                co_cells = [
                    {
                        "range": f"{self.co_desc_col_letter}{co_row}",
                        "values": [[new_desc]],
                    }
                    for co_row, _, new_desc in updates
                ]
                soma_cells = []
                for _, doc, new_desc in updates:
                    doc_norm = normalize_document_value(doc)
                    for s_row in self.soma_doc_to_rows.get(doc_norm, []):
                        soma_cells.append({
                            "range": f"{self.soma_desc_col_letter}{s_row}",
                            "values": [[new_desc]],
                        })

                if co_cells:
                    self.ws_contaordem.batch_update(co_cells, value_input_option=ValueInputOption.user_entered)
                if soma_cells:
                    self.ws_soma.batch_update(soma_cells, value_input_option=ValueInputOption.user_entered)
                time.sleep(0.5)
                return
            except Exception as e:
                logger.warning(f"Tentativa {attempt} de batch_update falhou: {e}. Aguardando retry...")
                time.sleep(attempt * 2)
        raise RuntimeError("Falha persistente ao atualizar Google Sheets em batch.")


def main():
    parser = argparse.ArgumentParser(description="Aplica resolução de DESCRIÇÃO SOMA duplicada no mesmo dia.")
    parser.add_argument("--apply", action="store_true", help="Aplica alterações no SOMA, CONTAORDEM e sheet SOMA.")
    parser.add_argument("--limit", type=int, default=0, help="Limita o número de registos a processar (0 = todos).")
    parser.add_argument("--group-date", type=str, default="", help="Filtra por data específica (DD/MM/AAAA).")
    args = parser.parse_args()

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    if not auth.login():
        raise SystemExit("Falha ao autenticar no SOMA.")
    audit = AuditService(settings=settings, http=http, sheets=sheets)
    resolver = ReconciliationResolver(settings, http, sheets, audit)

    values = sheets._ws.get_all_values()
    headers = [str(h).strip() for h in values[0]]
    indices = {norm_basic(h): i for i, h in enumerate(headers)}
    origem_idx = indices.get(norm_basic("ORIGEM"))
    desc_soma_idx = indices.get(norm_basic("DESCRIÇÃO SOMA"))
    desc_idx = indices.get(norm_basic("DESCRIÇÃO"))
    doc_idx = indices.get(norm_basic("DOC. SOMA"))
    dt_idx = indices.get(norm_basic("DATA MOV."))
    id_idx = indices.get(norm_basic("ID_INTERNO"))
    proc_idx = indices.get(norm_basic("PROCESSO"))

    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r_num, row in enumerate(values[1:], start=2):
        origem = row[origem_idx] if origem_idx < len(row) else ""
        if "duplicada no mesmo dia" in origem:
            doc = normalize_document_value(row[doc_idx] if doc_idx < len(row) else "")
            dt = normalize_date_str(row[dt_idx] if dt_idx < len(row) else "")
            desc = (row[desc_soma_idx] if desc_soma_idx < len(row) else "").strip() or (row[desc_idx] if desc_idx < len(row) else "").strip()
            base = strip_date_prefix(strip_suffix_n(desc)).strip()
            id_int = row[id_idx] if id_idx < len(row) else ""
            proc = row[proc_idx] if proc_idx < len(row) else ""

            if args.group_date and dt != normalize_date_str(args.group_date):
                continue

            groups[(dt, norm_basic(base))].append({
                "row_number": r_num,
                "data_mov": dt,
                "id_interno": id_int,
                "processo": proc,
                "doc_soma": doc,
                "desc": desc,
                "base": base,
                "raw_origem": origem,
            })

    total_linhas = sum(len(v) for v in groups.values())
    print("=" * 80)
    print("RESOLUÇÃO DE SEQUENCIAIS DUPLICADOS NO MESMO DIA")
    print(f"Modo: {'APPLY (Produção)' if args.apply else 'SIMULAÇÃO (Dry-Run)'}")
    print(f"Total de grupos: {len(groups)}")
    print(f"Total de linhas elegíveis: {total_linhas}")
    if args.limit:
        print(f"Limite de execução: {args.limit} registos")
    print("=" * 80)

    sheet_updater = SheetBatchUpdater(sheets) if args.apply else None

    plans: List[DuplicatePlan] = []
    soma_date_cache: Dict[str, List[Any]] = {}

    applied_count = 0
    maintained_count = 0
    blocked_count = 0
    total_processed = 0

    for (dt, base_norm), group_items in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        base_clean = group_items[0]["base"]

        # Busca itens no SOMA na data dt
        if dt not in soma_date_cache:
            time.sleep(0.3)
            soma_date_cache[dt] = audit.search_by_periodo(dt)
        soma_items_day = soma_date_cache[dt]

        # Filtra semelhantes no SOMA no mesmo dia
        similar_soma = [
            it for it in soma_items_day
            if similar_base(base_clean, it.descricao)
        ]
        soma_by_doc = {normalize_document_value(it.codigo): it for it in similar_soma}

        # Sequenciais já usados no SOMA no dia
        used_numbers: Set[int] = set()
        for it in similar_soma:
            num = extract_suffix_n(it.descricao)
            if num is not None:
                used_numbers.add(num)

        group_items_sorted = sorted(group_items, key=lambda x: x["row_number"])
        allocated_in_group: Set[int] = set()
        next_available = 1

        def get_next_seq(existing_used: Set[int]) -> int:
            nonlocal next_available
            while next_available in existing_used or next_available in allocated_in_group:
                next_available += 1
            allocated_in_group.add(next_available)
            return next_available

        print(f"\n--- GRUPO: {dt} | '{base_clean}' ({len(group_items_sorted)} linhas) ---")
        print(f"  Semelhantes no SOMA no dia: {[f'{it.codigo}:{it.descricao}' for it in similar_soma]}")

        group_pending_updates: List[Tuple[int, str, str]] = []

        for item in group_items_sorted:
            if args.limit and total_processed >= args.limit:
                break

            r_num = item["row_number"]
            doc = item["doc_soma"]
            curr_desc = item["desc"]
            curr_seq = extract_suffix_n(curr_desc)

            soma_item = soma_by_doc.get(doc)
            if not soma_item and doc:
                time.sleep(0.2)
                soma_item = audit.search_by_codigo(doc)

            soma_dt = soma_item.data if soma_item else "N/A"
            soma_desc = soma_item.descricao if soma_item else "N/A"

            soma_seq = extract_suffix_n(soma_desc) if soma_item else None

            # Identifica itens em SOMA com o mesmo sequencial
            soma_with_curr_seq = [it for it in similar_soma if extract_suffix_n(it.descricao) == curr_seq]

            can_keep = (
                curr_seq is not None
                and curr_seq not in allocated_in_group
                and (
                    len(soma_with_curr_seq) == 0
                    or (len(soma_with_curr_seq) == 1 and normalize_document_value(soma_with_curr_seq[0].codigo) == doc)
                )
            )

            if can_keep:
                allocated_in_group.add(curr_seq)
                proposed = f"{base_clean} N{curr_seq:03d}"
                action = "MANTER"
                reason = f"Sequencial N{curr_seq:03d} mantido (sem colisão)"
            elif soma_seq is not None and soma_seq not in allocated_in_group and sum(1 for it in similar_soma if extract_suffix_n(it.descricao) == soma_seq) <= 1:
                allocated_in_group.add(soma_seq)
                proposed = f"{base_clean} N{soma_seq:03d}"
                action = "MANTER" if norm_basic(curr_desc) == norm_basic(proposed) else "ATUALIZAR"
                reason = f"Sequencial N{soma_seq:03d} alinhado com o SOMA"
            else:
                new_seq = get_next_seq(used_numbers)
                proposed = f"{base_clean} N{new_seq:03d}"
                action = "ATUALIZAR"
                reason = f"Sequencial colidente ou duplicado -> atribuído N{new_seq:03d}"

            plan = DuplicatePlan(
                row_number=r_num,
                data_mov=dt,
                id_interno=item["id_interno"],
                processo=item["processo"],
                doc_soma=doc,
                current_desc=curr_desc,
                proposed_desc=proposed,
                soma_date=soma_dt,
                soma_desc=soma_desc,
                action=action,
                reason=reason,
            )
            plans.append(plan)
            total_processed += 1

            print(f"  Linha {r_num} | ID: {item['id_interno']} | DOC: {doc}")
            print(f"    Atual:    '{curr_desc}'")
            print(f"    Proposto: '{proposed}' [{action}] ({reason})")
            print(f"    SOMA:     dt={soma_dt}, desc='{soma_desc}'")

            if action == "MANTER":
                maintained_count += 1
                # Se sheets estão diferentes da proposta normalizada, sincroniza
                if args.apply and (curr_desc != proposed or (soma_desc and soma_desc != proposed)):
                    if soma_desc and soma_desc != proposed:
                        ok = resolver.update_soma_description(doc, proposed)
                        if not ok:
                            logger.error(f"Falha ao alinhar descrição no SOMA para DOC {doc}")
                    group_pending_updates.append((r_num, doc, proposed))
                continue

            # Ação ATUALIZAR
            if not doc:
                blocked_count += 1
                print("    STATUS: BLOQUEADO (DOC. SOMA vazio)")
                continue

            if args.apply:
                time.sleep(0.3)
                if soma_desc and norm_basic(soma_desc) == norm_basic(proposed):
                    ok = True
                    print(f"    STATUS: SOMA JÁ ESTÁ ATUALIZADO -> '{proposed}'")
                else:
                    ok = resolver.update_soma_description(doc, proposed)
                if not ok:
                    blocked_count += 1
                    print(f"    STATUS: BLOQUEADO (Falha ao atualizar/validar DOC {doc} no SOMA)")
                    continue

                group_pending_updates.append((r_num, doc, proposed))
                applied_count += 1
                if not (soma_desc and norm_basic(soma_desc) == norm_basic(proposed)):
                    print(f"    STATUS: SOMA ATUALIZADO COM SUCESSO -> '{proposed}'")
            else:
                applied_count += 1
                print(f"    STATUS: SIMULADO -> seria atualizado para '{proposed}'")

        # Se estamos aplicando e há atualizações pendentes para o grupo, grava nas sheets
        if args.apply and group_pending_updates and sheet_updater:
            print(f"  -> Gravando batch de {len(group_pending_updates)} registo(s) nas sheets CONTAORDEM e SOMA...")
            sheet_updater.apply_batch(group_pending_updates)
            print("  -> Batch gravado com sucesso nas sheets.")

        if args.limit and total_processed >= args.limit:
            print(f"\nLimite de {args.limit} registos atingido. Interrompendo processamento.")
            break

    print("\n" + "=" * 80)
    print("RESUMO FINAL DA EXECUÇÃO")
    print(f"Modo: {'APPLY' if args.apply else 'SIMULAÇÃO'}")
    print(f"Total processado: {total_processed}")
    print(f"Mantidos: {maintained_count}")
    print(f"Atualizados: {applied_count}")
    print(f"Bloqueados: {blocked_count}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    main()
