"""Auditoria SÓ DE LEITURA dos centros de custos no SOMA + DRY_RUN do plano de migração.

Não cria, altera, inativa nem elimina nada no SOMA. Faz apenas pedidos GET e as
pesquisas AJAX usadas pelas próprias páginas do portal.

Uso (no servidor, na raiz do projeto):
    .venv/bin/python -u scripts/audit_cost_centers.py [--usage] [--out FICHEIRO.json]

--usage  conta lançamentos por centro de custo (pesquisa de Entradas/Saídas; mais lento)
--out    grava o relatório completo em JSON (inventário, descoberta, DRY_RUN)

Não existe modo APPLY: a escrita só será implementada depois de aprovação explícita.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.audit_service import AuditService
from services.cost_center_service import (
    CostCenter,
    build_dry_run,
    find_duplicates,
    parse_select_options,
    summarize,
)

LIST_PAGE = "?mod=ivv&exec=centro_custos"
FOCUS = re.compile(r"centro|custo|departamento", re.I)


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html or "").split())


def discover_page(http, base: str, path: str) -> dict:
    """Descreve uma página: formulários, campos, tabelas, links e endpoints JS."""
    resp = http.get(base + path)
    html = resp.text
    info = {
        "path": path,
        "http": resp.status_code,
        "bytes": len(html),
        "titulo": text_of((re.search(r"<title>(.*?)</title>", html, re.S | re.I) or [None, ""])[1])[:120],
        "forms": [f[:220] for f in re.findall(r"<form\b[^>]*>", html, re.I) if "search" not in f],
        "campos": [],
        "selects": {},
        "tabela_cabecalho": [],
        "linhas_tabela": [],
        "links_modulo": sorted(set(re.findall(r"""href=["'](\?mod=ivv&(?:amp;)?exec=[a-z_]*(?:centro|custo)[^"']*)["']""", html, re.I))),
        "endpoints_js": {},
    }
    for tag, name in re.findall(r"<(input|select|textarea)\b[^>]*\bname=[\"']([^\"']+)[\"']", html, re.I):
        if name not in info["campos"] and name not in ("email", "site-search"):
            info["campos"].append(name)
    for name, body in re.findall(r"<select\b[^>]*name=[\"']([^\"']+)[\"'][^>]*>(.*?)</select>", html, re.I | re.S):
        opts = re.findall(r"<option\b[^>]*value=[\"']([^\"']*)[\"'][^>]*>(.*?)</option>", body, re.S)
        info["selects"][name] = [(v, text_of(t)) for v, t in opts][:15]
    header = re.search(r"<thead\b.*?</thead>", html, re.S | re.I)
    if header:
        info["tabela_cabecalho"] = [text_of(c) for c in re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", header.group(0), re.S)]
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [text_of(c) for c in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S | re.I)]
        if not cells:
            continue
        ids = sorted(set(re.findall(r"""(?:\bid|value|data-id)=["'](\d+)["']""", row)))
        actions = sorted(set(re.findall(r"""class=["'][^"']*\b(bnt_excluir|btn_excluir|btn_editar|editar|excluir|inativar|ativar|status)\b""", row, re.I)))
        info["linhas_tabela"].append({"celulas": cells, "ids": ids, "acoes": actions})
    scripts = re.findall(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", html, re.I)
    for src in scripts:
        if "themes/js/" not in src or "template" in src:
            continue
        try:
            js = http.get(urljoin(resp.url, src)).text
        except Exception as exc:  # noqa: BLE001
            info["endpoints_js"][src] = f"falha: {exc}"
            continue
        endpoints = sorted(set(re.findall(r"[\"']((?:sys/)?(?:app|post)/[A-Za-z0-9_]+\.php)[\"']", js)))
        snippets = []
        for ep in endpoints:
            if not FOCUS.search(ep):
                continue
            for m in list(re.finditer(re.escape(ep), js))[:2]:
                snippets.append(" ".join(js[max(0, m.start() - 500): m.end() + 900].split()))
        info["endpoints_js"][src] = {"endpoints": endpoints, "trechos": snippets}
    return info


def count_usage(http, settings, base: str, center_id: str, parser: AuditService) -> tuple:
    """Lançamentos de Entradas/Saídas com este centro de custo (pesquisa usada pela página)."""
    codes = set()
    for t_d in ("1", "0"):
        payload = {
            "pesquisa": "", "filtro": "descricao", "id_inst": settings.institution_id, "tipo": "2",
            "v": "0", "s": "2", "t_d": t_d, "cc": center_id, "c": "",
        }
        resp = http.post_ajax(f"{base}sys/post/buscarEntradasSaidas.php", data=payload)
        codes.update(item.codigo for item in parser._parse_search_table(resp.text))
    return len(codes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usage", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.apply:
        print("APPLY não está disponível: esta auditoria é só de leitura e a escrita exige aprovação explícita.")
        return 2

    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    if not SomaAuthenticator(settings, http).login(force=True):
        print("Falha no login (credenciais do .env não impressas).")
        return 1
    base = settings.site_base_url.rstrip("/") + "/"
    report = {"gerado_em": datetime.now().isoformat(timespec="seconds"), "instituicao": settings.institution_id}

    # A. Descoberta do portal
    page = discover_page(http, base, LIST_PAGE)
    report["pagina_lista"] = page
    data_links = [l.replace("&amp;", "&") for l in page["links_modulo"] if "dados" in l]
    form_path = data_links[0].split("&ID=")[0] if data_links else "?mod=ivv&exec=centro_custos_dados"
    report["pagina_formulario_novo"] = discover_page(http, base, form_path)
    edit_link = next((l for l in data_links if "ID=" in l), "")
    if edit_link:
        report["pagina_formulario_editar"] = discover_page(http, base, edit_link)

    # B. Inventário (catálogo oficial usado pelos formulários)
    cat = http.post_ajax(f"{base}sys/post/buscarCentrodeCustoSelect.php", data={"id": settings.institution_id})
    inventory = parse_select_options(cat.text)
    if args.usage:
        parser = AuditService(settings, http)
        total = count_usage(http, settings, base, "-1", parser)
        report["uso_total_pesquisa_todos"] = total
        for c in inventory:
            c.usage = count_usage(http, settings, base, c.id, parser)
    report["inventario"] = [
        {"id_interno": c.id, "rotulo": c.label, "codigo": c.code, "descricao": c.description,
         "lancamentos": c.usage}
        for c in inventory
    ]
    report["duplicados"] = find_duplicates(inventory)

    # C. DRY_RUN
    ops = build_dry_run(inventory)
    report["dry_run"] = [op.as_dict() for op in sorted(ops, key=lambda o: (o.order or 10**6, o.status))]
    report["resumo"] = summarize(ops)

    # Saída resumida (sem dados sensíveis)
    print(f"== PÁGINA {LIST_PAGE}: HTTP {page['http']}, título='{page['titulo']}'")
    print(f"   forms: {page['forms']}")
    print(f"   campos: {page['campos']}")
    print(f"   cabeçalho tabela: {page['tabela_cabecalho']}")
    print(f"   linhas tabela: {len(page['linhas_tabela'])}; exemplo: {page['linhas_tabela'][:3]}")
    print(f"   links do módulo: {page['links_modulo'][:10]}")
    for key in ("pagina_formulario_novo", "pagina_formulario_editar"):
        if key in report:
            f = report[key]
            print(f"== {key}: {f['path']} HTTP {f['http']} campos={f['campos']} selects={ {k: v[:6] for k, v in f['selects'].items()} }")
    for src, info in {**page["endpoints_js"], **report["pagina_formulario_novo"]["endpoints_js"]}.items():
        if isinstance(info, dict):
            print(f"== JS {src}: {info['endpoints']}")
            for s in info["trechos"][:4]:
                print(f"   ...{s[:1400]}...")
    print(f"\n== INVENTÁRIO: {len(inventory)} centros | duplicados: {report['duplicados']['codigos']}")
    if args.usage:
        print(f"   (pesquisa sem filtro devolve {report['uso_total_pesquisa_todos']} lançamentos — se igual por centro, o filtro cc não é suportado)")
    for c in inventory:
        print(f"   ID {c.id:>6} | {c.label:60s} | lanç.: {c.usage if c.usage is not None else '-'}")
    print(f"\n== DRY_RUN: {report['resumo']}")
    for op in report["dry_run"]:
        print(f"   {str(op['ordem'] or '-'):>3} {op['estado']:15s} {op['operacao']:15s} ID {op['id_interno'] or '-':>6} "
              f"{(op['codigo_atual'] + ' ' + op['descricao_atual'])[:50]:50s} -> {op['codigo_proposto']} {op['descricao_proposta'][:40]}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nRelatório completo: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
