from __future__ import annotations

import argparse
import calendar
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import clean_amount_for_comparison, is_entrada_ou_saida, norm_basic, normalize_date_str
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only-divergent", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    rows = sheets.get_all_rows(only_entrada_saida=True)

    targets = []
    months = set()
    for row in rows:
        if not is_entrada_ou_saida(row.tipo):
            continue
        if args.only_divergent and not row.auditoria.strip().lower().startswith("divergente"):
            continue
        data_mov = normalize_date_str(row.data_mov)
        description = str(row.descricao_soma or "").strip()
        if data_mov:
            date_object = datetime.strptime(data_mov, "%d/%m/%Y")
            months.add((date_object.year, date_object.month))
        targets.append((row, data_mov, description))

    soma_by_date_description = defaultdict(dict)
    for month_index, (year, month) in enumerate(sorted(months), start=1):
        last_day = calendar.monthrange(year, month)[1]
        start = f"01/{month:02d}/{year}"
        end = f"{last_day:02d}/{month:02d}/{year}"
        monthly_items = []
        for date_type in ("1", "0"):
            payload = {
                "pesquisa": "",
                "filtro": "descricao",
                "id_inst": settings.institution_id,
                "tipo": "2",
                "v": "1",
                "s": "2",
                "t_d": date_type,
                "cc": "-1",
                "c": "",
                "i": start,
                "f": end,
            }
            response = http.post_ajax(
                f"{settings.site_base_url.rstrip('/')}/sys/post/buscarEntradasSaidas.php",
                data=payload,
            )
            monthly_items.extend(audit._parse_search_table(response.text))

        for item in monthly_items:
            code = str(item.codigo).strip()
            key = (normalize_date_str(item.data), norm_basic(item.descricao))
            soma_by_date_description[key][code] = item
        print(f"SOMA: mês {month_index}/{len(months)} carregado ({month:02d}/{year})", flush=True)

    updates = []
    counts = Counter()
    samples = defaultdict(list)
    for row, data_mov, description in targets:
        if not data_mov or not description:
            category = "Divergente"
            reason = "data ou DESCRIÇÃO SOMA vazia"
            audit_status = f"Divergente: {reason}"
        else:
            candidates = list(soma_by_date_description.get((data_mov, norm_basic(description)), {}).values())
            if len(candidates) > 1:
                category = "Erro"
                audit_status = "Erro"
                reason = f"{len(candidates)} correspondências"
            elif not candidates:
                category = "Divergente"
                reason = "nenhuma correspondência"
                audit_status = f"Divergente: {reason}"
            elif clean_amount_for_comparison(candidates[0].valor) == clean_amount_for_comparison(row.importancia):
                category = "Conferido"
                audit_status = "Conferido"
                reason = f"DOC {candidates[0].codigo}"
            else:
                category = "Divergente"
                reason = f"valor SOMA {candidates[0].valor} != folha {row.importancia}"
                audit_status = (
                    f"Divergente: valor SOMA={candidates[0].valor} "
                    f"!= CONTAORDEM={row.importancia}"
                )
        counts[category] += 1
        if len(samples[category]) < 20:
            samples[category].append((row.row_number, reason))
        updates.append({"row_idx": row.row_number, "auditoria": audit_status})

    print(f"Total: {len(updates)}")
    for status in ("Conferido", "Erro", "Divergente"):
        print(f"{status}: {counts[status]}")
        for row_number, reason in samples[status]:
            print(f"  Linha {row_number}: {reason}")
    if not args.apply:
        print("Simulação: nenhuma atualização aplicada.")
        return
    sheets.batch_update_audit_records(updates)
    print(f"Atualizações aplicadas: {len(updates)}")


if __name__ == "__main__":
    main()
