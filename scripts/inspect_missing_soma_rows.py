from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import clean_amount_for_comparison, norm_basic
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


def main() -> None:
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    rows = sheets.get_all_rows(only_entrada_saida=True)
    dates = sorted({row.data_mov for row in rows if row.auditoria.startswith("Erro SOMA: ausentes=")})
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    audit = AuditService(settings, http, sheets)
    total = 0
    for date in dates:
        date_rows = [row for row in rows if row.data_mov == date]
        site_counter = Counter(
            (norm_basic(item.tipo), norm_basic(item.descricao), clean_amount_for_comparison(item.valor))
            for item in audit.search_by_periodo(date)
            if norm_basic(item.tipo) in ("entrada", "saida")
        )
        for row in date_rows:
            key = (
                norm_basic(row.tipo.value),
                norm_basic(row.descricao_soma or row.descricao),
                clean_amount_for_comparison(row.importancia),
            )
            if site_counter[key]:
                site_counter[key] -= 1
                continue
            total += 1
            print(
                f"data={date} linha={row.row_number} tipo={row.tipo.value} valor={row.importancia} "
                f"descrição={row.descricao_soma or row.descricao} ID={row.id_interno} DOC={row.doc_soma}",
                flush=True,
            )
    print(f"Total ausentes={total}")


if __name__ == "__main__":
    main()
