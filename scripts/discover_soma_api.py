#!/usr/bin/env python
"""
Descobrir endpoints AJAX/API do SOMA que carregam dados de caixas.
"""

from __future__ import annotations

import re
import sys
import logging
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("discover_soma_api")


def extract_ajax_calls(html: str) -> list:
    """Extrai URLs de AJAX/fetch calls do HTML e JavaScript."""
    ajax_patterns = [
        r'(?:url|href)\s*:\s*["\']([^"\']*caixa[^"\']*)["\']',
        r'(?:fetch|post|get)\s*\(\s*["\']([^"\']*caixa[^"\']*)["\']',
        r'buscar[^<]*php',
        r'listar[^<]*php',
        r'obter[^<]*php',
        r'loadCaixa',
        r'getCaixa',
        r'/sys/post/[^"\'\s]+',
    ]

    urls = set()

    for pattern in ajax_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
        urls.update(matches)

    return sorted(list(urls))


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 100)
    print("DESCOBERTA DE ENDPOINTS AJAX/API DO SOMA")
    print("=" * 100)

    # Autenticação
    print("\n[1/3] Autenticando no SOMA...")
    if not auth.login():
        print("❌ Falha na autenticação")
        return 1
    print("✅ Autenticado")

    # Acesso à página
    print("\n[2/3] Acessando página de caixas...")
    base_url = settings.site_base_url.rstrip("/")
    caixas_url = f"{base_url}/index.php?mod=ivv&exec=caixas"

    try:
        response = http.get(caixas_url, timeout=20)
        if response.status_code != 200:
            print(f"❌ Erro: Status {response.status_code}")
            return 1
        print(f"✅ Página acessada ({len(response.text)} bytes)")
    except Exception as e:
        print(f"❌ Erro: {e}")
        return 1

    # Descoberta de endpoints
    print("\n[3/3] Analisando código HTML/JS para encontrar endpoints...")

    ajax_urls = extract_ajax_calls(response.text)

    print("\n" + "=" * 100)
    print("ENDPOINTS DETECTADOS")
    print("=" * 100)

    if ajax_urls:
        print(f"\n✅ Encontrados {len(ajax_urls)} endpoints potenciais:\n")

        for i, url in enumerate(ajax_urls, 1):
            print(f"{i}. {url}")
    else:
        print("\n⚠️  Nenhum endpoint óbvio encontrado nos padrões básicos")

    # Procura por scripts JavaScript
    print("\n" + "=" * 100)
    print("ANÁLISE DE SCRIPTS")
    print("=" * 100)

    # Procura por <script> tags com src
    script_srcs = re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', response.text)

    print(f"\n✅ Encontrados {len(script_srcs)} arquivos JavaScript:\n")

    for src in script_srcs[:10]:
        print(f"   • {src}")

    if len(script_srcs) > 10:
        print(f"   ... e mais {len(script_srcs) - 10}")

    # Procura por inline scripts com palavras-chave
    print("\n" + "=" * 100)
    print("PALAVRAS-CHAVE ENCONTRADAS NO JS INLINE")
    print("=" * 100)

    inline_scripts = re.findall(r'<script[^>]*>(.*?)</script>', response.text, re.DOTALL)

    keywords = ['fetch', 'ajax', 'post', 'get', 'caixa', 'nivel', 'api', 'buscar', 'listar']

    print(f"\nAnalisando {len(inline_scripts)} blocos de JavaScript inline...\n")

    found_keywords = {}
    for kw in keywords:
        for script in inline_scripts:
            if kw.lower() in script.lower():
                if kw not in found_keywords:
                    found_keywords[kw] = 0
                found_keywords[kw] += 1

    if found_keywords:
        print("Palavras-chave encontradas:")
        for kw, count in sorted(found_keywords.items(), key=lambda x: x[1], reverse=True):
            print(f"   • '{kw}': {count} ocorrência(s)")
    else:
        print("Nenhuma palavra-chave encontrada")

    # Procura por URLs relativas com .php
    print("\n" + "=" * 100)
    print("ENDPOINTS .PHP ENCONTRADOS")
    print("=" * 100)

    php_urls = set(re.findall(r'(["\'])(?:\.\.\/)?(?:sys/post/)?([^"\']*\.php)\1', response.text))

    if php_urls:
        print(f"\n✅ Encontrados {len(php_urls)} endpoints .php:\n")

        for quote, url in sorted(php_urls):
            if 'caixa' in url.lower() or 'nivel' in url.lower():
                print(f"   🎯 {url}")
            else:
                print(f"      {url}")

    # Testa endpoints conhecidos
    print("\n" + "=" * 100)
    print("TESTANDO ENDPOINTS CONHECIDOS")
    print("=" * 100)

    test_endpoints = [
        "../sys/post/listarCaixasNivelizacao.php",
        "../sys/post/buscarCaixasNivelizacao.php",
        "../sys/post/getCaixas.php",
        "../sys/post/obterCaixas.php",
        "sys/post/listarCaixasNivelizacao.php",
        "sys/post/buscarCaixasNivelizacao.php",
    ]

    print(f"\nTestando {len(test_endpoints)} endpoints...\n")

    for endpoint in test_endpoints:
        test_url = f"{base_url}/{endpoint}".replace("//", "/").replace("////", "//")

        try:
            # Tenta POST com dados
            r = http.post_ajax(test_url, data={"id_inst": settings.institution_id}, timeout=5)

            if r.status_code == 200 and len(r.text) > 10:
                print(f"✅ {endpoint}")
                print(f"   Status: {r.status_code}")
                print(f"   Resposta (primeiros 150 chars): {r.text[:150]}")
                print()
        except:
            pass

    print("\n" + "=" * 100)
    print("CONCLUSÃO")
    print("=" * 100)
    print("""
✅ CONEXÃO AO SOMA: FUNCIONAL
✅ AUTENTICAÇÃO: VALIDADA
⚠️  DADOS SÃO CARREGADOS DINAMICAMENTE VIA JAVASCRIPT

Os dados das caixas são carregados via uma chamada AJAX/JavaScript.
Para capturá-los, é necessário:
1. Identificar o endpoint exato que retorna os dados
2. Fazer uma requisição diretamente a esse endpoint
3. Fazer parsing da resposta (JSON ou HTML parcial)

Se nenhum endpoint .php foi encontrado acima, os dados podem estar em:
   • JavaScript codificado inline (window.data, localStorage, etc.)
   • Chamadas para um backend Node.js/API REST
   • Página renderizada server-side com dados já inclusos
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
