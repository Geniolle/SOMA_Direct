#!/usr/bin/env python
"""
Teste de conexão ao site SOMA e extração de dados via DOM/HTML.
Acesso APENAS DE LEITURA - sem modificações.
"""

from __future__ import annotations

import re
import sys
import logging
from pathlib import Path
from typing import Optional

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_soma_dom")


def parse_caixas_dom(html: str) -> dict:
    """Extrai valores de caixas do DOM HTML da página SOMA."""
    caixas = {}

    # Procura por elementos que contenham "CAIXA" e valores monetários
    # Padrão: procura por divs/spans que contenham dados de caixas

    # Padrão 1: Procura por text nodes com CAIXA seguido de valor
    caixa_pattern = r'(?:CAIXA[^<]*?MONTEPIO[^<]*?).*?(?:[\d.,]+\s*€?)'
    matches = re.findall(caixa_pattern, html, re.IGNORECASE | re.DOTALL)

    if matches:
        logger.info(f"Padrão 1: Encontrados {len(matches)} blocos com CAIXA MONTEPIO")
        for match in matches[:3]:
            print(f"  Match: {match[:100]}")

    # Padrão 2: Procura por divs com class contendo "caixa" ou "box"
    div_pattern = r'<div[^>]*class="[^"]*(?:caixa|box|card)[^"]*"[^>]*>(.*?)</div>'
    divs = re.findall(div_pattern, html, re.IGNORECASE | re.DOTALL)

    logger.info(f"Padrão 2: Encontrados {len(divs)} divs com class contendo 'caixa/box'")

    for div in divs[:5]:
        if 'MONTEPIO' in div.upper():
            # Extrai nome e valor
            name_match = re.search(r'(?:CAIXA[^<]*?MONTEPIO[^<]*?)(?=<|$)', div, re.IGNORECASE)
            valor_match = re.search(r'([\d.,]+)\s*€?', div)

            if name_match and valor_match:
                name = name_match.group(0).strip()
                valor = valor_match.group(1)

                name_clean = re.sub(r'<[^>]+>', '', name).strip()
                if 'MONTEPIO' in name_clean.upper():
                    caixas[name_clean] = valor
                    logger.info(f"✅ Extraído via Padrão 2: {name_clean[:50]} = {valor}")

    # Padrão 3: Procura por spans com valores (common em interfaces modernas)
    span_pattern = r'<span[^>]*>(.*?MONTEPIO.*?)</span>.*?<span[^>]*>(.*?[€\d].*?)</span>'
    spans = re.findall(span_pattern, html, re.IGNORECASE | re.DOTALL)

    logger.info(f"Padrão 3: Encontrados {len(spans)} pares span (nome/valor)")

    for name, valor in spans[:3]:
        name_clean = re.sub(r'<[^>]+>', '', name).strip()
        valor_clean = re.sub(r'<[^>]+>', '', valor).strip()

        if name_clean and valor_clean:
            caixas[name_clean] = valor_clean
            logger.info(f"✅ Extraído via Padrão 3: {name_clean[:50]} = {valor_clean}")

    # Padrão 4: Procura por data attributes (common em React/Vue)
    data_pattern = r'data-(?:caixa|box|item)="([^"]*MONTEPIO[^"]*)"[^>]*>.*?(?:R\$|€|valor)\s*([^<\n]*[\d.,]+)'
    data_matches = re.findall(data_pattern, html, re.IGNORECASE | re.DOTALL)

    logger.info(f"Padrão 4: Encontrados {len(data_matches)} elementos com data attributes")

    for name, valor in data_matches[:3]:
        if name and valor:
            caixas[name.strip()] = valor.strip()
            logger.info(f"✅ Extraído via Padrão 4: {name.strip()[:50]} = {valor.strip()}")

    return caixas


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 100)
    print("TESTE DE CONEXÃO AO SOMA - ACESSO DOM (LEITURA APENAS)")
    print("=" * 100)

    # Passo 1: Autenticação
    print("\n[1/3] Conectando e autenticando no SOMA...")
    if not auth.login():
        print("❌ FALHA: Não foi possível autenticar")
        return 1
    print("✅ SUCESSO: Autenticado no SOMA")

    # Passo 2: Acesso à página de caixas
    print("\n[2/3] Acessando página de caixas (execução: caixas)...")
    base_url = settings.site_base_url.rstrip("/")
    caixas_url = f"{base_url}/index.php?mod=ivv&exec=caixas"

    try:
        response = http.get(caixas_url, timeout=20)

        if response.status_code != 200:
            print(f"❌ FALHA: Status {response.status_code}")
            return 1

        print(f"✅ SUCESSO: Página acessada (Status: {response.status_code}, Tamanho: {len(response.text)} bytes)")

    except Exception as e:
        print(f"❌ FALHA: Erro de conexão: {e}")
        return 1

    # Passo 3: Parsing do DOM
    print("\n[3/3] Extraindo dados via parsing do DOM HTML...")

    caixas_data = parse_caixas_dom(response.text)

    print("\n" + "=" * 100)
    print("RESULTADO DO PARSING DO DOM")
    print("=" * 100)

    if caixas_data:
        print(f"\n✅ SUCESSO: Foram extraídos {len(caixas_data)} caixa(s) do DOM:\n")

        for caixa_name, valor in caixas_data.items():
            print(f"   📦 {caixa_name}")
            print(f"      Valor: {valor}\n")

        return 0
    else:
        print("\n⚠️  AVISO: Nenhum caixa foi extraído com os padrões atuais")
        print("\n   Investigando estrutura do DOM...\n")

        # Debug: Mostra snippets do HTML
        print("   Buscando 'MONTEPIO' no HTML:")
        montepio_lines = []
        for i, line in enumerate(response.text.split('\n')):
            if 'MONTEPIO' in line:
                montepio_lines.append((i, line[:120]))

        if montepio_lines:
            print(f"   ✅ Encontradas {len(montepio_lines)} linhas com 'MONTEPIO':\n")
            for line_num, line_content in montepio_lines[:10]:
                print(f"      Linha {line_num}: {line_content}")
        else:
            print("   ❌ 'MONTEPIO' não encontrado no HTML da página")
            print("\n   Primeiros 500 chars do HTML:")
            print(f"   {response.text[:500]}")

        return 1

    print("\n" + "=" * 100)
    print("CONCLUSÃO")
    print("=" * 100)
    print("""
✅ CONEXÃO AO SOMA: FUNCIONAL
✅ AUTENTICAÇÃO: VALIDADA
✅ ACESSO AO DOM: CONFIRMADO
✅ PARSING: FUNCIONANDO

Os dados são acessíveis via HTTP GET na URL:
  https://verbodavida.info/IVV/index.php?mod=ivv&exec=caixas

O script pode extrair informações do DOM HTML da página.
    """)


if __name__ == "__main__":
    sys.exit(main())
