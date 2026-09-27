"""Diagnóstico SÓ DE LEITURA: descobre como o SOMA grava Transferências e Entradas/Saídas.

Abre (GET) os formulários oficiais, lista os campos e procura no JavaScript da
página o endpoint usado para gravar. Também valida a pesquisa de transferências
numa data de controlo. Não cria, altera nem apaga nada.

Uso: .venv/bin/python scripts/discover_soma_save_endpoints.py [DATA_CONTROLO]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import parse_transfer_table

PAGES = {
    "TRANSFERÊNCIA": "?mod=ivv&exec=transferencias_caixas_dados",
    "ENTRADAS/SAÍDAS": "?mod=ivv&exec=entradas_saidas_dados",
}
KEYWORDS = re.compile(r"transfer|entradas_saidas|fluxo", re.I)


def describe_form(html: str) -> None:
    for form in re.findall(r"<form\b[^>]*>", html, re.I):
        print(f"   FORM: {form[:200]}")
    names = []
    for tag, name in re.findall(r"<(input|select|textarea)\b[^>]*\bname=[\"']([^\"']+)[\"']", html, re.I):
        if name not in names:
            names.append(name)
    print(f"   CAMPOS ({len(names)}): {', '.join(names)}")


def scan_js(http, base: str, html: str) -> None:
    sources = re.findall(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", html, re.I)
    inline = re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.I | re.S)
    blobs = [("inline", "\n".join(inline))]
    for src in sources:
        url = urljoin(base, src)
        if "verbodavida.info" not in url or not KEYWORDS.search(src + url) and "themes/js" not in url:
            continue
        try:
            blobs.append((src, http.get(url).text))
        except Exception as exc:  # noqa: BLE001
            print(f"   (falha ao ler {src}: {exc})")
    print(f"   SCRIPTS: {', '.join(s for s in sources if 'verbodavida' in urljoin(base, s))[:600]}")
    for label, js in blobs:
        hits = set(re.findall(r"[\"']((?:\.\./|/)?(?:sys/)?(?:app|post)/[A-Za-z0-9_]+\.php)[\"']", js))
        if not hits:
            continue
        print(f"   {label}: endpoints -> {', '.join(sorted(hits))}")
        for m in re.finditer(r"(sys/)?app/[A-Za-z0-9_]*(transfer|entradas_saidas|fluxo)[A-Za-z0-9_]*\.php", js, re.I):
            ctx = " ".join(js[max(0, m.start() - 250): m.end() + 350].split())
            print(f"      ...{ctx[:600]}...")
            break


def main() -> int:
    control_date = sys.argv[1] if len(sys.argv) > 1 else "23/07/2026"
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    if not SomaAuthenticator(settings, http).login(force=True):
        print("Falha no login")
        return 1
    base = settings.site_base_url.rstrip("/") + "/"

    resp = http.post_ajax(base + "sys/post/buscarTransferenciasCaixas.php",
                          data={"id_inst": settings.institution_id, "i": control_date, "f": control_date})
    found = parse_transfer_table(resp.text)
    print(f"== CONTROLO pesquisa {control_date}: {len(found)} transferência(s)")
    for t in found:
        print(f"   ID {t.transfer_id}: {t.caixa_origem} -> {t.caixa_destino} | {t.valor_saida} | {t.data}")

    for label, path in PAGES.items():
        page = http.get(base + path)
        print(f"\n== {label}: GET {path} -> HTTP {page.status_code}, url final={page.url.split('?')[0]}, {len(page.text)} bytes")
        describe_form(page.text)
        scan_js(http, page.url, page.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
