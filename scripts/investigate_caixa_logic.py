#!/usr/bin/env python
"""
Investigação profunda da lógica de cálculo do valor do caixa no SOMA.
Analisa: código JavaScript, estrutura HTML, endpoints, dados JSON.
"""

from __future__ import annotations

import re
import sys
import json
import logging
from pathlib import Path
from typing import Dict, List, Any

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("investigate_caixa_logic")


def extract_json_objects(html: str) -> List[Dict]:
    """Extrai objetos JSON do HTML."""
    json_objects = []

    # Padrão 1: var data = {...}
    pattern1 = r'(?:var|let|const)\s+\w+\s*=\s*(\{[^;]*?\});'
    matches1 = re.findall(pattern1, html, re.DOTALL)

    # Padrão 2: JSON.parse
    pattern2 = r'JSON\.parse\s*\(\s*["\']([^"\']*)["\']'
    matches2 = re.findall(pattern2, html)

    # Padrão 3: Strings JSON inline
    pattern3 = r'(\{[^{}]*"(?:caixa|valor|saldo|nivel)"[^{}]*\})'
    matches3 = re.findall(pattern3, html, re.IGNORECASE)

    all_matches = matches1 + matches2 + matches3

    for match in all_matches:
        try:
            if isinstance(match, tuple):
                match = match[0]

            obj = json.loads(match)
            json_objects.append(obj)
            logger.info(f"✅ Objeto JSON extraído: {str(obj)[:100]}")
        except json.JSONDecodeError:
            pass
        except:
            pass

    return json_objects


def extract_javascript_logic(html: str) -> Dict[str, List[str]]:
    """Extrai trechos de código JavaScript que lidam com cálculos."""
    logic = {
        "somas": [],
        "subtrações": [],
        "multiplicações": [],
        "divisões": [],
        "atribuições_caixa": [],
        "calculos": [],
        "fetches": [],
        "posts": [],
    }

    # Extrai scripts inline
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)

    combined_js = ' '.join(scripts)

    # Padrão: Somas/Subtrações com caixa/valor/saldo
    soma_pattern = r'(?:caixa|valor|saldo)[.\w]*\s*(?:\+=|-=|\+|-)\s*[\w.]+(?:caixa|valor|saldo|[\d.,]+)'
    somas = re.findall(soma_pattern, combined_js, re.IGNORECASE)
    logic["somas"] = somas

    # Padrão: Cálculos complexos
    calculo_pattern = r'(?:var|let|const)\s+(\w+)\s*=\s*([^;]+(?:caixa|valor|saldo)[^;]*);'
    calculos = re.findall(calculo_pattern, combined_js, re.IGNORECASE)
    logic["calculos"] = [f"{var} = {expr.strip()}" for var, expr in calculos]

    # Padrão: Atribuições de caixa
    atrib_pattern = r'(?:caixa|valor|saldo)[.\w]*\s*=\s*([^;]+);'
    atribs = re.findall(atrib_pattern, combined_js, re.IGNORECASE)
    logic["atribuições_caixa"] = atribs

    # Padrão: Fetch/AJAX chamadas
    fetch_pattern = r'fetch\s*\(\s*["\']([^"\']*)["\']'
    fetches = re.findall(fetch_pattern, combined_js)
    logic["fetches"] = fetches

    # Padrão: POST chamadas
    post_pattern = r'(?:\.post|ajax.*?url\s*:\s*)["\']([^"\']*)["\']'
    posts = re.findall(post_pattern, combined_js, re.IGNORECASE)
    logic["posts"] = posts

    return logic


def extract_html_components(html: str) -> Dict[str, List[str]]:
    """Extrai componentes HTML que podem representar caixas e valores."""
    components = {
        "div_caixa": [],
        "span_valor": [],
        "data_attributes": [],
        "ids_relevantes": [],
        "classes_relevantes": [],
    }

    # Padrão: divs com classe contendo "caixa"
    divs = re.findall(r'<div[^>]*class="[^"]*caixa[^"]*"[^>]*id="([^"]*)"', html, re.IGNORECASE)
    components["div_caixa"] = divs

    # Padrão: spans com valores
    spans = re.findall(r'<span[^>]*class="[^"]*(?:valor|saldo|amount)[^"]*"[^>]*>([^<]*[€$\d][^<]*)</span>', html, re.IGNORECASE)
    components["span_valor"] = spans

    # Padrão: data attributes
    data_attrs = re.findall(r'data-(?:caixa|valor|saldo|nivel)="([^"]*)"', html, re.IGNORECASE)
    components["data_attributes"] = data_attrs

    # IDs relevantes
    ids = re.findall(r'id="([^"]*(?:caixa|valor|saldo|nivel)[^"]*)"', html, re.IGNORECASE)
    components["ids_relevantes"] = ids

    # Classes relevantes
    classes = re.findall(r'class="[^"]*(?:caixa|valor|saldo|nivel)[^"]*"', html, re.IGNORECASE)
    components["classes_relevantes"] = list(set(classes))

    return components


def test_api_endpoints(http: ResilientSession, settings: Settings, base_url: str) -> Dict[str, Any]:
    """Testa endpoints de API que podem retornar dados de caixas."""
    results = {}

    endpoints_to_test = [
        # Endpoints comuns para caixas
        "sys/post/listarCaixasNivelizacao.php",
        "sys/post/buscarCaixasNivelizacao.php",
        "sys/post/getCaixas.php",
        "sys/post/obterCaixas.php",
        "sys/post/caixas.php",
        "api/caixas",
        "api/v1/caixas",
        # Endpoints alternativos
        "index.php?mod=ivv&exec=gerenciarCaixas&ajax=1",
        "index.php?mod=ivv&exec=caixas&ajax=1",
        # Endpoints baseados em padrões comuns
        "sys/post/buscar.php?tipo=caixa",
        "sys/post/listar.php?tipo=caixa",
    ]

    for endpoint in endpoints_to_test:
        try:
            url = f"{base_url}/{endpoint}".replace("//", "/").replace("////", "//")

            # Tenta POST
            response = http.post_ajax(
                url,
                data={
                    "id_inst": settings.institution_id,
                    "v": "1",
                    "ajax": "1",
                },
                timeout=5
            )

            if response.status_code == 200 and len(response.text) > 20:
                # Tenta parsear como JSON
                try:
                    data = json.loads(response.text)
                    results[endpoint] = {
                        "status": response.status_code,
                        "type": "json",
                        "data": data,
                        "preview": str(data)[:200]
                    }
                    logger.info(f"✅ {endpoint} (JSON)")
                except:
                    # Se não for JSON, trata como HTML
                    results[endpoint] = {
                        "status": response.status_code,
                        "type": "html",
                        "size": len(response.text),
                        "preview": response.text[:300]
                    }
                    logger.info(f"✅ {endpoint} (HTML)")
        except Exception as e:
            logger.debug(f"❌ {endpoint}: {str(e)[:50]}")

    return results


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 110)
    print("INVESTIGAÇÃO DA LÓGICA DE CÁLCULO DO VALOR DO CAIXA - SOMA")
    print("=" * 110)

    # Autenticação
    print("\n[1/5] Autenticando...")
    if not auth.login():
        print("❌ Falha na autenticação")
        return 1
    print("✅ Autenticado")

    # Acesso à página
    print("\n[2/5] Acessando página de caixas...")
    base_url = settings.site_base_url.rstrip("/")
    caixas_url = f"{base_url}/index.php?mod=ivv&exec=caixas"

    try:
        response = http.get(caixas_url, timeout=20)
        if response.status_code != 200:
            print(f"❌ Erro: Status {response.status_code}")
            return 1
        html = response.text
        print(f"✅ Página obtida ({len(html)} bytes)")
    except Exception as e:
        print(f"❌ Erro: {e}")
        return 1

    # Análise 1: Extração de objetos JSON
    print("\n[3/5] Procurando objetos JSON...")
    json_objects = extract_json_objects(html)
    print(f"✅ Encontrados {len(json_objects)} objetos JSON")

    if json_objects:
        print("\n   Primeiros objetos JSON encontrados:")
        for i, obj in enumerate(json_objects[:3], 1):
            preview = str(obj)[:150]
            print(f"   {i}. {preview}...")

    # Análise 2: Lógica JavaScript
    print("\n[4/5] Analisando lógica JavaScript...")
    js_logic = extract_javascript_logic(html)

    print("\n   📋 LÓGICA ENCONTRADA:")
    print(f"      • Operações de soma/subtração: {len(js_logic['somas'])}")
    if js_logic['somas']:
        for soma in js_logic['somas'][:3]:
            print(f"        - {soma}")

    print(f"      • Cálculos complexos: {len(js_logic['calculos'])}")
    if js_logic['calculos']:
        for calc in js_logic['calculos'][:3]:
            print(f"        - {calc}")

    print(f"      • Atribuições a caixa: {len(js_logic['atribuições_caixa'])}")
    if js_logic['atribuições_caixa']:
        for atrib in js_logic['atribuições_caixa'][:3]:
            print(f"        - {atrib}")

    print(f"      • Chamadas Fetch: {len(js_logic['fetches'])}")
    if js_logic['fetches']:
        for fetch in js_logic['fetches'][:3]:
            print(f"        - {fetch}")

    print(f"      • Chamadas POST/AJAX: {len(js_logic['posts'])}")
    if js_logic['posts']:
        for post in js_logic['posts'][:3]:
            print(f"        - {post}")

    # Análise 3: Componentes HTML
    print("\n[5/5] Analisando estrutura HTML...")
    components = extract_html_components(html)

    print("\n   📦 COMPONENTES HTML ENCONTRADOS:")
    print(f"      • Divs com 'caixa': {len(components['div_caixa'])}")
    if components['div_caixa']:
        for div_id in components['div_caixa'][:5]:
            print(f"        - ID: {div_id}")

    print(f"      • Spans com valores: {len(components['span_valor'])}")
    if components['span_valor']:
        for span in components['span_valor'][:5]:
            print(f"        - {span[:50]}")

    print(f"      • Data attributes: {len(components['data_attributes'])}")
    if components['data_attributes']:
        for attr in components['data_attributes'][:5]:
            print(f"        - {attr}")

    print(f"      • IDs relevantes: {len(components['ids_relevantes'])}")
    if components['ids_relevantes']:
        for id_elem in components['ids_relevantes'][:5]:
            print(f"        - {id_elem}")

    print(f"      • Classes relevantes: {len(components['classes_relevantes'])}")
    if components['classes_relevantes']:
        for cls in components['classes_relevantes'][:5]:
            print(f"        - {cls}")

    # Teste de endpoints
    print("\n" + "=" * 110)
    print("TESTE DE ENDPOINTS DE API")
    print("=" * 110)

    print("\nTestando endpoints que podem retornar dados de caixas...")
    api_results = test_api_endpoints(http, settings, base_url)

    if api_results:
        print(f"\n✅ Encontrados {len(api_results)} endpoints ativos:\n")

        for endpoint, data in api_results.items():
            print(f"   🔗 {endpoint}")
            print(f"      Status: {data['status']}")
            print(f"      Tipo: {data['type']}")
            print(f"      Preview: {data.get('preview', data.get('size', 'N/A'))[:80]}")
            print()
    else:
        print("\n⚠️  Nenhum endpoint retornou dados")

    # Relatório final
    print("\n" + "=" * 110)
    print("RESUMO DA LÓGICA DE CÁLCULO DO CAIXA")
    print("=" * 110)

    if js_logic['calculos'] or js_logic['somas']:
        print("""
✅ LÓGICA DETECTADA:

A página utiliza JavaScript para:
1. Buscar dados via AJAX/Fetch dos endpoints de API
2. Receber dados em JSON ou HTML parcial
3. Processar valores com operações de soma/subtração
4. Atualizar componentes HTML com os valores calculados

FLUXO TÍPICO:
   fetch() → API endpoint
       ↓
   Recebe: {caixa: {...}, saldos: [...], deduções: [...]}
       ↓
   Calcula: total_entrada - total_saida
       ↓
   Atualiza: DOM elementos com class="saldo" ou id="*-saldo"
    """)
    else:
        print("""
⚠️  LÓGICA NÃO COMPLETAMENTE MAPEADA

Possíveis razões:
1. JavaScript é minificado ou ofuscado
2. Dados vêm de servidor (renderização server-side)
3. Dados carregam dinamicamente após page load
4. Framework (React/Vue) gerencia a lógica

PRÓXIMAS AÇÕES:
1. Interceptar requisições HTTP com DevTools
2. Procurar por endpoints em redes alternativas (.net, .io)
3. Verificar localStorage/sessionStorage no navegador
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
