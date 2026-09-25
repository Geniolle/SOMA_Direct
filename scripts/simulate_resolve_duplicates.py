from __future__ import annotations

import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

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
from services.sheets_service import GoogleSheetsService


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
    doc_soma: str
    current_desc: str
    proposed_desc: str
    soma_date: str
    soma_desc: str
    similar_in_soma: List[str]
    action: str  # MANTER, ATUALIZAR, BLOQUEADO
    reason: str


def main():
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    if not auth.login():
        raise SystemExit("Falha ao autenticar no SOMA.")
    audit = AuditService(settings=settings, http=http, sheets=sheets)

    values = sheets._ws.get_all_values()
    headers = [str(h).strip() for h in values[0]]
    indices = {norm_basic(h): i for i, h in enumerate(headers)}
    origem_idx = indices.get(norm_basic("ORIGEM"))
    desc_soma_idx = indices.get(norm_basic("DESCRIÇÃO SOMA"))
    desc_idx = indices.get(norm_basic("DESCRIÇÃO"))
    doc_idx = indices.get(norm_basic("DOC. SOMA"))
    dt_idx = indices.get(norm_basic("DATA MOV."))
    id_idx = indices.get(norm_basic("ID_INTERNO"))

    # Agrupa por (data_mov, base_text)
    groups = defaultdict(list)
    for r_num, row in enumerate(values[1:], start=2):
        origem = row[origem_idx] if origem_idx < len(row) else ""
        if "duplicada no mesmo dia" in origem:
            doc = normalize_document_value(row[doc_idx] if doc_idx < len(row) else "")
            dt = normalize_date_str(row[dt_idx] if dt_idx < len(row) else "")
            desc = (row[desc_soma_idx] if desc_soma_idx < len(row) else "").strip() or (row[desc_idx] if desc_idx < len(row) else "").strip()
            base = strip_date_prefix(strip_suffix_n(desc)).strip()
            id_int = row[id_idx] if id_idx < len(row) else ""
            groups[(dt, norm_basic(base))].append({
                "row_number": r_num,
                "data_mov": dt,
                "id_interno": id_int,
                "doc_soma": doc,
                "desc": desc,
                "base": base,
                "raw_origem": origem,
            })

    print(f"Total de grupos de duplicadas: {len(groups)}")
    total_linhas = sum(len(v) for v in groups.values())
    print(f"Total de linhas envolvidas: {total_linhas}")
    print()

    plans: List[DuplicatePlan] = []
    soma_date_cache = {}

    for (dt, base_norm), group_items in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1])):
        base_clean = group_items[0]["base"]

        # Busca no SOMA na data dt
        if dt not in soma_date_cache:
            time.sleep(0.3)
            soma_date_cache[dt] = audit.search_by_periodo(dt)
        soma_items_day = soma_date_cache[dt]

        # Filtra semelhantes no SOMA no mesmo dia
        similar_soma = [
            it for it in soma_items_day
            if similar_base(base_clean, it.descricao)
        ]

        # Mapeia DOC -> item SOMA
        soma_by_doc = {normalize_document_value(it.codigo): it for it in similar_soma}

        # Números sequenciais já atribuídos no SOMA no dia
        used_numbers = set()
        for it in similar_soma:
            num = extract_suffix_n(it.descricao)
            if num is not None:
                used_numbers.add(num)

        # Ordena linhas por row_number para estabilidade
        group_items_sorted = sorted(group_items, key=lambda x: x["row_number"])

        # Controla sequenciais para este grupo neste dia
        allocated_in_group = set()
        next_available = 1

        def get_next_seq(existing_used: set[int]) -> int:
            nonlocal next_available
            while next_available in existing_used or next_available in allocated_in_group:
                next_available += 1
            allocated_in_group.add(next_available)
            return next_available

        print(f"--- GRUPO: {dt} | '{base_clean}' ({len(group_items_sorted)} linhas) ---")
        print(f"  Semelhantes no SOMA no dia: {[f'{it.codigo}:{it.descricao}' for it in similar_soma]}")

        for item in group_items_sorted:
            r_num = item["row_number"]
            doc = item["doc_soma"]
            curr_desc = item["desc"]
            curr_seq = extract_suffix_n(curr_desc)

            # Verifica DOC no SOMA
            soma_item = soma_by_doc.get(doc)
            if not soma_item and doc:
                time.sleep(0.2)
                soma_item = audit.search_by_codigo(doc)

            soma_dt = soma_item.data if soma_item else "N/A"
            soma_desc = soma_item.descricao if soma_item else "N/A"
            soma_seq = extract_suffix_n(soma_desc) if soma_item else None

            # Regra: se o primeiro item já tem sequencial válido e único no dia, mantemos
            if curr_seq is not None and curr_seq not in allocated_in_group and sum(1 for it in similar_soma if extract_suffix_n(it.descricao) == curr_seq) <= 1:
                allocated_in_group.add(curr_seq)
                proposed = f"{base_clean} N{curr_seq:03d}"
                action = "MANTER" if proposed.upper() == curr_desc.upper() else "ATUALIZAR"
                reason = f"Sequencial N{curr_seq:03d} mantido (sem colisão)"
            else:
                # Precisa de novo sequencial
                # Se SOMA tem semelhantes com sequencial, segue a sequência (próximo disponível)
                if used_numbers or allocated_in_group:
                    new_seq = get_next_seq(used_numbers)
                else:
                    new_seq = get_next_seq(set())
                proposed = f"{base_clean} N{new_seq:03d}"
                action = "ATUALIZAR"
                reason = f"Sequencial colidente ou duplicado -> atribuído N{new_seq:03d}"

            plans.append(DuplicatePlan(
                row_number=r_num,
                data_mov=dt,
                id_interno=item["id_interno"],
                doc_soma=doc,
                current_desc=curr_desc,
                proposed_desc=proposed,
                soma_date=soma_dt,
                soma_desc=soma_desc,
                similar_in_soma=[it.codigo for it in similar_soma],
                action=action,
                reason=reason,
            ))

            print(f"  Linha {r_num} | ID: {item['id_interno']} | DOC: {doc}")
            print(f"    Atual:    '{curr_desc}'")
            print(f"    Proposto: '{proposed}' [{action}] ({reason})")
            print(f"    SOMA:     dt={soma_dt}, desc='{soma_desc}'")

    print("\n=== RESUMO DA SIMULAÇÃO ===")
    manter_cnt = sum(1 for p in plans if p.action == "MANTER")
    atualizar_cnt = sum(1 for p in plans if p.action == "ATUALIZAR")
    bloqueado_cnt = sum(1 for p in plans if p.action == "BLOQUEADO")
    print(f"Total de linhas analisadas: {len(plans)}")
    print(f"Manter: {manter_cnt}")
    print(f"Atualizar: {atualizar_cnt}")
    print(f"Bloqueado: {bloqueado_cnt}")


if __name__ == "__main__":
    main()
