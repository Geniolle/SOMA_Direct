"""Aplica a reorganização de centros de custos às folhas CCVV e CONTAORDEM (AppTesouraria).

Ordem obrigatória: SOMA primeiro, folhas depois. Por isso o modo --apply verifica,
antes de escrever, que o catálogo do SOMA já contém os rótulos novos.

    .venv/bin/python -u scripts/migrate_cost_center_sheets.py            # DRY_RUN (padrão)
    .venv/bin/python -u scripts/migrate_cost_center_sheets.py --apply    # escreve

CCVV:        renomeia no lugar os rótulos antigos e acrescenta os centros novos do SOMA.
CONTAORDEM:  substitui, na coluna CENTRO DE CUSTO, cada rótulo antigo pelo novo (de/para).
Não toca em nenhuma outra coluna nem folha.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain.cost_center_plan import TARGET_PLAN, label_migration
from services.cost_center_service import norm

CCVV_SHEET = "CCVV"
CENTER_COLUMN = "CENTRO DE CUSTO"


def col_letter(idx: int) -> str:
    out = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def plan_ccvv(values: List[List[str]], de_para: Dict[str, str], soma_labels: List[str]) -> Tuple[List[dict], List[str]]:
    """Devolve (renomeações no lugar, rótulos a acrescentar)."""
    by_norm = {norm(k): v for k, v in de_para.items()}
    updates = []
    present = set()
    for row_number, row in enumerate(values[1:], start=2):
        current = (row[0] if row else "").strip()
        if not current:
            continue
        new = by_norm.get(norm(current))
        if new and new != current:
            updates.append({"range": f"A{row_number}", "values": [[new]], "old": current})
            present.add(norm(new))
        else:
            present.add(norm(current))
    planned = {norm(t.label): t.label for t in TARGET_PLAN}
    appends = [label for label in soma_labels if norm(label) in planned and norm(label) not in present]
    return updates, appends


def plan_contaordem(values: List[List[str]], de_para: Dict[str, str]) -> Tuple[List[dict], Counter]:
    header = [h.strip() for h in values[0]]
    col = next((i for i, h in enumerate(header) if norm(h) == norm(CENTER_COLUMN)), None)
    if col is None:
        raise RuntimeError(f"Coluna {CENTER_COLUMN} não encontrada na CONTAORDEM")
    by_norm = {norm(k): v for k, v in de_para.items()}
    letter = col_letter(col + 1)
    updates, counts = [], Counter()
    for row_number, row in enumerate(values[1:], start=2):
        current = (row[col] if col < len(row) else "").strip()
        new = by_norm.get(norm(current)) if current else None
        if new and new != current:
            updates.append({"range": f"{letter}{row_number}", "values": [[new]]})
            counts[f"{current} -> {new}"] += 1
    return updates, counts


def batch_write(ws, updates: List[dict]) -> None:
    payload = [{"range": u["range"], "values": u["values"]} for u in updates]
    for start in range(0, len(payload), 400):
        chunk = payload[start:start + 400]
        for attempt in range(5):
            try:
                ws.batch_update(chunk, value_input_option="USER_ENTERED")
                break
            except Exception as exc:  # noqa: BLE001
                if "429" in str(exc) and attempt < 4:
                    time.sleep(20)
                else:
                    raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from config.settings import Settings
    from core.auth import SomaAuthenticator
    from core.http_session import ResilientSession
    from services.cost_center_service import parse_select_options
    from services.sheets_service import GoogleSheetsService

    settings = Settings.from_env()
    de_para = label_migration()

    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    if not SomaAuthenticator(settings, http).login(force=True):
        print("Falha no login no SOMA (credenciais não impressas).")
        return 1
    base = settings.site_base_url.rstrip("/") + "/"
    catalog = parse_select_options(http.post_ajax(f"{base}sys/post/buscarCentrodeCustoSelect.php",
                                                  data={"id": settings.institution_id}).text)
    soma_labels = [c.label for c in catalog]
    soma_norm = {norm(l) for l in soma_labels}
    missing_in_soma = sorted({new for new in de_para.values() if norm(new) not in soma_norm})

    sheets = GoogleSheetsService(settings)
    ccvv = sheets._sh.worksheet(CCVV_SHEET)
    ccvv_values = ccvv.get_all_values()
    co_values = sheets._ws.get_all_values()

    ccvv_updates, ccvv_appends = plan_ccvv(ccvv_values, de_para, soma_labels)
    co_updates, co_counts = plan_contaordem(co_values, de_para)

    print(f"== De/para: {len(de_para)} rótulos | SOMA: {len(catalog)} centros no catálogo")
    print(f"== CCVV: {len(ccvv_updates)} renomeações, {len(ccvv_appends)} centros novos a acrescentar")
    for u in ccvv_updates:
        print(f"   {u['range']}: {u['old']} -> {u['values'][0][0]}")
    for label in ccvv_appends:
        print(f"   + {label}")
    print(f"== CONTAORDEM: {len(co_updates)} células a atualizar")
    for change, n in sorted(co_counts.items()):
        print(f"   {n:5d}  {change}")

    if not args.apply:
        if missing_in_soma:
            print(f"\nATENÇÃO: o SOMA ainda não tem {len(missing_in_soma)} rótulo(s) novo(s); --apply será recusado até o SOMA estar migrado.")
        print("\nDRY_RUN: nada foi escrito.")
        return 0
    if missing_in_soma:
        print("\nRECUSADO: o SOMA ainda não tem estes rótulos (migrar o SOMA primeiro):")
        for label in missing_in_soma:
            print(f"   - {label}")
        return 3

    if ccvv_updates:
        batch_write(ccvv, ccvv_updates)
    if ccvv_appends:
        ccvv.append_rows([[label] for label in ccvv_appends], value_input_option="USER_ENTERED")
    if co_updates:
        batch_write(sheets._ws, co_updates)

    # Confirmação: reler e verificar que não sobrou nenhum rótulo antigo.
    left_co = plan_contaordem(sheets._ws.get_all_values(), de_para)[0]
    left_ccvv = plan_ccvv(ccvv.get_all_values(), de_para, soma_labels)
    print(f"\nAPLICADO. Restam na CONTAORDEM: {len(left_co)} | CCVV: {len(left_ccvv[0])} renomeações, {len(left_ccvv[1])} em falta.")
    return 0 if not left_co and not left_ccvv[0] and not left_ccvv[1] else 4


if __name__ == "__main__":
    raise SystemExit(main())
