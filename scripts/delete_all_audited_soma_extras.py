from __future__ import annotations

import argparse
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import normalize_date_str
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    extras_by_date: dict[str, set[str]] = defaultdict(set)
    for row in sheets.get_all_rows(only_entrada_saida=True):
        audit_text = row.auditoria.strip()
        if not audit_text.startswith("Erro SOMA:"):
            continue
        date = normalize_date_str(row.data_mov)
        for code in re.findall(r"DOC (\d+)", audit_text):
            extras_by_date[date].add(code)

    expected = sum(len(codes) for codes in extras_by_date.values())
    print(f"Datas={len(extras_by_date)} extras_auditados={expected}", flush=True)

    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    audit = AuditService(settings, http, sheets)
    delete_url = f"{settings.site_base_url.rstrip('/')}/sys/app/entradas_saidas.php"
    confirmed: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []

    for index, (date, expected_codes) in enumerate(sorted(extras_by_date.items()), start=1):
        site_codes = {item.codigo for item in audit.search_by_periodo(date)}
        for code in sorted(expected_codes):
            (confirmed if code in site_codes else missing).append((date, code))
        print(f"Validado {index}/{len(extras_by_date)} data={date}", flush=True)

    print(f"Confirmados={len(confirmed)} já_ausentes={len(missing)}", flush=True)
    if not args.apply:
        print("Simulação: nenhuma exclusão aplicada.")
        return

    deleted = 0
    for date, code in confirmed:
        response = http.post_ajax(delete_url, data={"id": code, "excluir": "1"})
        try:
            result = response.json()
        except Exception as error:
            raise RuntimeError(f"Resposta inválida ao excluir DOC {code}: {response.text[:200]}") from error
        if int(result.get("status", 0)) != 1:
            raise RuntimeError(f"Falha ao excluir DOC {code} de {date}: {result}")
        deleted += 1
        print(f"Excluído {deleted}/{len(confirmed)} data={date} DOC={code}", flush=True)
        time.sleep(0.15)

    print(f"Exclusões concluídas={deleted}", flush=True)


if __name__ == "__main__":
    main()
