from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import norm_basic, normalize_date_str


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--codes", nargs="*")
    args = parser.parse_args()

    target_date = normalize_date_str(args.date)
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")

    payload = {
        "pesquisa": "",
        "filtro": "descricao",
        "id_inst": settings.institution_id,
        "tipo": "2",
        "v": "1",
        "s": "2",
        "t_d": "1",
        "cc": "-1",
        "c": "",
        "i": target_date,
        "f": target_date,
    }
    candidates = []
    for date_mode in ("1", "0"):
        payload["t_d"] = date_mode
        response = http.post_ajax(
            f"{settings.site_base_url.rstrip('/')}/sys/post/buscarEntradasSaidas.php",
            data=payload,
        )
        for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", response.text, re.DOTALL | re.IGNORECASE):
            cells = [
                html.unescape(re.sub(r"<[^>]+>", " ", cell)).strip()
                for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, re.DOTALL | re.IGNORECASE)
            ]
            requested_codes = set(args.codes or [])
            if len(cells) < 9 or (
                norm_basic(cells[7]) != "em aberto" and cells[2] not in requested_codes
            ):
                continue
            actions_cell = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, re.DOTALL | re.IGNORECASE)[1]
            action_tags = re.findall(r"<a\b[^>]*>", actions_cell, re.IGNORECASE)
            if len(action_tags) < 2:
                raise RuntimeError(f"DOC {cells[2]} sem segundo botão de ação: {action_tags}")
            action = action_tags[1]
            if not any(candidate[0] == cells[2] for candidate in candidates):
                candidates.append((cells[2], cells[4], cells[5], action))

    print(f"Data={target_date} candidatos={len(candidates)}")
    for code, description, value, action in candidates:
        print(f"DOC={code} valor={value} descrição={description} ação={action}")

    if not args.apply:
        page = http.get(f"{settings.site_base_url}?mod=ivv&exec=entradas_saidas")
        script_sources = re.findall(r"<script\b[^>]*src=[\"']([^\"']+)[\"']", page.text, re.IGNORECASE)
        for source in script_sources:
            script_url = urljoin(settings.site_base_url, html.unescape(source))
            if "verbodavida.info" not in script_url:
                continue
            script_response = http.get(script_url)
            if "bnt_excluir" in script_response.text:
                position = script_response.text.index("bnt_excluir")
                print("JS_EXCLUSAO=" + script_response.text[max(0, position - 500):position + 1200])
        print("Simulação: nenhuma exclusão aplicada.")
        return
    if requested_codes:
        candidates = [candidate for candidate in candidates if candidate[0] in requested_codes]
        found_codes = {candidate[0] for candidate in candidates}
        if found_codes != requested_codes:
            raise RuntimeError(
                f"Exclusão cancelada: pedidos={sorted(requested_codes)} encontrados={sorted(found_codes)}"
            )
    elif not candidates:
        raise RuntimeError("Exclusão cancelada: nenhum documento EM ABERTO encontrado")

    delete_url = f"{settings.site_base_url.rstrip('/')}/sys/app/entradas_saidas.php"
    for code, _, _, _ in candidates:
        delete_response = http.post_ajax(delete_url, data={"id": code, "excluir": "1"})
        try:
            delete_result = delete_response.json()
        except Exception as error:
            raise RuntimeError(f"Resposta inválida ao excluir DOC {code}: {delete_response.text[:200]}") from error
        if int(delete_result.get("status", 0)) != 1:
            raise RuntimeError(f"Falha ao excluir DOC {code}: {delete_result}")
        print(f"Excluído: DOC={code}")


if __name__ == "__main__":
    main()
