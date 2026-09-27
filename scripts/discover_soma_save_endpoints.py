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
    "LISTA TRANSFERÊNCIAS": "?mod=ivv&exec=transferencias_caixas",
    "ENTRADAS/SAÍDAS": "?mod=ivv&exec=entradas_saidas_dados",
}
# Trechos de JS a mostrar por inteiro (pedido AJAX + tratamento da resposta).
FOCUS = ("buscarTransferenciasCaixas", "sys/app/transferencias_caixas.php", "sys/app/entradas_saidas.php")
KEYWORDS = re.compile(r"transfer|entradas_saidas|fluxo", re.I)


def show_hidden_inputs(html: str) -> None:
    for tag in re.findall(r"<input\b[^>]*type=[\"']hidden[\"'][^>]*>", html, re.I):
        name = re.search(r"name=[\"']([^\"']+)", tag)
        value = re.search(r"value=[\"']([^\"']*)", tag)
        if name and name.group(1) not in ("email",):
            print(f"   HIDDEN {name.group(1)}={value.group(1) if value else ''!r}")
    for sel in ("id_caixa_origem", "id_caixa_destino"):
        m = re.search(rf"<select\b[^>]*name=[\"']{sel}[\"'][^>]*>(.*?)</select>", html, re.I | re.S)
        if m:
            opts = re.findall(r"<option\b[^>]*value=[\"']([^\"']*)[\"'][^>]*>(.*?)</option>", m.group(1), re.S)
            print(f"   SELECT {sel}: {[(v, ' '.join(t.split())) for v, t in opts][:8]}")


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
        for key in FOCUS:
            for m in list(re.finditer(re.escape(key), js))[:3]:
                ctx = " ".join(js[max(0, m.start() - 900): m.end() + 1400].split())
                print(f"      [{key}] ...{ctx}...")


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
        show_hidden_inputs(page.text)
        scan_js(http, page.url, page.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
