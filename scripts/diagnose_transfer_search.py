"""Diagnóstico SÓ DE LEITURA da pesquisa de transferências no SOMA.

Compara a resposta de buscarTransferenciasCaixas.php para a mesma data com
intervalos diferentes. Não cria, altera nem apaga nada.

Uso: .venv/bin/python scripts/diagnose_transfer_search.py 15/09/2026
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import normalize_date_str, parse_transfer_table


def query(http, settings, start: str, end: str):
    url = settings.site_base_url.rstrip("/") + "/sys/post/buscarTransferenciasCaixas.php"
    resp = http.post_ajax(url, data={"id_inst": settings.institution_id, "i": start, "f": end})
    return resp, parse_transfer_table(resp.text)


def main() -> int:
    target = normalize_date_str(sys.argv[1] if len(sys.argv) > 1 else "15/09/2026")
    day = datetime.strptime(target, "%d/%m/%Y")
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    if not SomaAuthenticator(settings, http).login(force=True):
        print("Falha no login")
        return 1

    fmt = lambda d: d.strftime("%d/%m/%Y")
    windows = {
        "mesmo dia (i=f)": (fmt(day), fmt(day)),
        "dia-1 .. dia+1": (fmt(day - timedelta(days=1)), fmt(day + timedelta(days=1))),
        "mês inteiro": (fmt(day.replace(day=1)), fmt((day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))),
    }
    for label, (start, end) in windows.items():
        resp, transfers = query(http, settings, start, end)
        on_day = [t for t in transfers if normalize_date_str(t.data) == target]
        print(f"\n== {label}: i={start} f={end} -> HTTP {resp.status_code}, {len(resp.text)} bytes, "
              f"{len(re.findall(r'<tr', resp.text, re.I))} <tr>, {len(transfers)} transferências, {len(on_day)} em {target}")
        for t in on_day:
            print(f"   ID {t.transfer_id}: {t.caixa_origem} -> {t.caixa_destino} | {t.valor_saida} | {t.data} | obs={t.observacao!r}")
        if not transfers:
            snippet = " ".join(re.sub(r"<[^>]+>", " ", resp.text).split())[:300]
            print(f"   corpo: {snippet!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
