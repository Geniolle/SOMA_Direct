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
from domain.models import clean_amount_for_comparison, norm_basic, normalize_date_str, normalize_document_value
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, http, sheets)
    targets = [row for row in sheets.get_all_rows(only_entrada_saida=True) if norm_basic(row.auditoria) == "perfeito"]

    months = set()
    for row in targets:
        normalized_date = normalize_date_str(row.data_mov)
        if normalized_date:
            date_object = datetime.strptime(normalized_date, "%d/%m/%Y")
            months.add((date_object.year, date_object.month))

    soma_by_date_description = defaultdict(list)
    for month_index, (year, month) in enumerate(sorted(months), start=1):
        last_day = calendar.monthrange(year, month)[1]
        start = f"01/{month:02d}/{year}"
        end = f"{last_day:02d}/{month:02d}/{year}"
        items_by_code = {}
        for date_type in ("1", "0"):
            response = http.post_ajax(
                f"{settings.site_base_url.rstrip('/')}/sys/post/buscarEntradasSaidas.php",
                data={
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
                },
            )
            for item in audit._parse_search_table(response.text):
                items_by_code[str(item.codigo).strip()] = item
        for item in items_by_code.values():
            key = (normalize_date_str(item.data), norm_basic(item.descricao))
            soma_by_date_description[key].append(item)
        print(f"SOMA: mês {month_index}/{len(months)} carregado ({month:02d}/{year})", flush=True)

    updates = []
    counts = Counter()
    for row in targets:
        data_mov = normalize_date_str(row.data_mov)
        description = str(row.descricao_soma or "").strip()
        expected_doc = normalize_document_value(row.doc_soma)
        candidates = soma_by_date_description.get((data_mov, norm_basic(description)), [])
        doc_matches = [item for item in candidates if normalize_document_value(item.codigo) == expected_doc]

        if not data_mov or not description or not expected_doc:
            missing = []
            if not data_mov:
                missing.append("data")
            if not description:
                missing.append("descrição")
            if not expected_doc:
                missing.append("DOC. SOMA")
            status = f"Erro final: {'/'.join(missing)} vazio"
        elif not candidates:
            status = "Erro final: sem correspondência no SOMA para data + descrição"
        elif not doc_matches:
            found_docs = ",".join(sorted({normalize_document_value(item.codigo) for item in candidates}))
            status = f"Erro final: DOC CONTAORDEM={expected_doc} / SOMA={found_docs}"
        elif len(doc_matches) > 1:
            status = f"Erro final: DOC {expected_doc} duplicado no SOMA para data + descrição"
        elif clean_amount_for_comparison(doc_matches[0].valor) != clean_amount_for_comparison(row.importancia):
            status = f"Erro final: valor CONTAORDEM={row.importancia} / SOMA={doc_matches[0].valor}"
        else:
            status = "Concluido"

        counts[status.split(":", 1)[0]] += 1
        updates.append({"row_idx": row.row_number, "auditoria": status})

    print(f"Linhas Perfeito verificadas={len(targets)}")
    print(f"Concluido={counts['Concluido']}")
    print(f"Erros={counts['Erro final']}")
    if not args.apply:
        print("Simulação: nenhuma atualização aplicada.")
        return
    sheets.batch_update_audit_records(updates)
    print(f"Atualizações aplicadas={len(updates)}")


if __name__ == "__main__":
    main()
