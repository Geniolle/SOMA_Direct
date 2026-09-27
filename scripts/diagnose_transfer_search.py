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

    base = settings.site_base_url.rstrip("/") + "/"
    page = http.get(base + "?mod=ivv&exec=transferencias_caixas").text
    for name in ("vencimento_inicio", "vencimento_fim"):
        tag = re.search(rf"<input\b[^>]*name=[\"']{name}[\"'][^>]*>", page, re.I)
        print(f"INPUT {name}: {tag.group(0)[:250] if tag else 'não encontrado'}")
    sel = re.search(r"<select\b[^>]*name=[\"']id_inst[\"'][^>]*>(.*?)</select>", page, re.I | re.S)
    if sel:
        opts = re.findall(r"<option\b([^>]*)value=[\"']([^\"']*)[\"'][^>]*>(.*?)</option>", sel.group(1), re.S)
        print("SELECT id_inst:", [(v, " ".join(t.split()), "selected" in a) for a, v, t in opts][:10])
    print(f"settings.institution_id = {settings.institution_id}")

    fmt = lambda d, f: d.strftime(f)
    variants = {
        "dd/mm/aaaa dia": (fmt(day, "%d/%m/%Y"), fmt(day, "%d/%m/%Y")),
        "mm/dd/aaaa dia": (fmt(day, "%m/%d/%Y"), fmt(day, "%m/%d/%Y")),
        "aaaa-mm-dd dia": (fmt(day, "%Y-%m-%d"), fmt(day, "%Y-%m-%d")),
        "dd/mm/aaaa mês": (fmt(day.replace(day=1), "%d/%m/%Y"), fmt(day.replace(day=28), "%d/%m/%Y")),
        "vazio": ("", ""),
        "2000..2099": ("01/01/2000", "31/12/2099"),
    }
    for label, (start, end) in variants.items():
        resp, transfers = query(http, settings, start, end)
        on_day = [t for t in transfers if normalize_date_str(t.data) == target]
        dates = sorted({t.data for t in transfers})
        print(f"\n== {label}: i={start!r} f={end!r} -> {len(transfers)} transferências "
              f"({len(on_day)} em {target}); datas: {dates[:3]} ... {dates[-3:]}")
        for t in on_day[:6]:
            print(f"   ID {t.transfer_id}: {t.caixa_origem} -> {t.caixa_destino} | {t.valor_saida} | {t.data}")
        if not transfers:
            snippet = " ".join(re.sub(r"<[^>]+>", " ", resp.text).split())[:160]
            print(f"   corpo: {snippet!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
