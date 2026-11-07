"""Diagnóstico SÓ DE LEITURA: contagem de transferências no SOMA por ano e mês.

Uso: .venv/bin/python scripts/count_soma_transfers.py 2019 2026 [DD/MM/AAAA ...]
"""
from __future__ import annotations

import calendar
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import parse_transfer_table

LIMIT = 100  # o SOMA devolve no máximo 100 linhas por pesquisa


def main() -> int:
    first, last = int(sys.argv[1]), int(sys.argv[2])
    days = sys.argv[3:]
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    if not SomaAuthenticator(settings, http).login(force=True):
        print("Falha no login")
        return 1
    url = settings.site_base_url.rstrip("/") + "/sys/post/buscarTransferenciasCaixas.php"

    def search(i, f):
        return parse_transfer_table(http.post_ajax(url, data={"id_inst": settings.institution_id, "i": i, "f": f}).text)

    for year in range(first, last + 1):
        months = []
        for m in range(1, 13):
            n = len(search(f"01/{m:02d}/{year}", f"{calendar.monthrange(year, m)[1]:02d}/{m:02d}/{year}"))
            months.append(f"{m:02d}:{n}{'+' if n >= LIMIT else ''}")
        print(f"{year}: " + "  ".join(months))
    for d in days:
        found = search(d, d)
        print(f"\n{d}: {len(found)} transferência(s)")
        for t in found:
            print(f"   ID {t.transfer_id}: {t.caixa_origem} -> {t.caixa_destino} | {t.valor_saida} | {t.data} | {t.observacao!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
