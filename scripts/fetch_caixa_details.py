#!/usr/bin/env python
"""
Script para extrair os detalhes e composição do valor de um caixa específico no SOMA.
Acessa: https://verbodavida.info/IVV/index.php?mod=ivv&exec=caixas
"""

from __future__ import annotations

import re
import sys
import logging
from pathlib import Path
from typing import Dict, List, Optional

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fetch_caixa_details")


def fetch_gerenciar_caixas_data(http: ResilientSession, settings: Settings) -> Optional[str]:
    """Tenta buscar os dados de caixas usando diferentes métodos."""
    base_url = settings.site_base_url.rstrip("/")

    # Método 1: Acesso direto à página
    print("  [M1] Tentando acesso direto à página de caixas...")
    try:
        url = f"{base_url}/index.php?mod=ivv&exec=gerenciarCaixas"
        response = http.get(url)
        if response.status_code == 200 and len(response.text) > 5000:
            print(f"      ✅ Página 'gerenciarCaixas' acessada ({len(response.text)} bytes)")
            return response.text
    except Exception as e:
        logger.debug(f"Método 1 falhou: {e}")

    # Método 2: Page original (pode estar com dados inline)
    print("  [M2] Tentando página original 'caixas'...")
    try:
        url = f"{base_url}/index.php?mod=ivv&exec=caixas"
        response = http.get(url, timeout=15)
        if response.status_code == 200:
            print(f"      ✅ Página 'caixas' acessada ({len(response.text)} bytes)")
            return response.text
    except Exception as e:
        logger.debug(f"Método 2 falhou: {e}")

    # Método 3: AJAX
    print("  [M3] Tentando requisição AJAX...")
    try:
        url = f"{base_url}/../sys/post/listarCaixasNivelizacao.php"
        response = http.post_ajax(url, data={"id_inst": settings.institution_id})
        if response.status_code == 200 and response.text.strip():
            print(f"      ✅ AJAX 'listarCaixasNivelizacao' respondeu ({len(response.text)} bytes)")
            return response.text
    except Exception as e:
        logger.debug(f"Método 3 falhou: {e}")

    return None


def extract_caixas_and_movs(html: str) -> Dict[str, Dict]:
    """Extrai caixas e suas movimentações do HTML/JSON."""
    results = {}

    # Padrão 1: JSON inline (common em SPAs modernas)
    json_patterns = [
        r'var\s+caixas\s*=\s*(\[.*?\]);',
        r'"caixas"\s*:\s*(\[.*?\])',
        r'data\s*:\s*(\[.*?\])',
    ]

    for pattern in json_patterns:
        try:
            matches = re.findall(pattern, html, re.DOTALL)
            if matches:
                logger.info(f"Padrão JSON encontrado: {pattern[:30]}...")
                print(f"\n🔍 Padrão JSON encontrado:")
                for match in matches[:2]:
                    print(f"   {match[:150]}...")
        except:
            pass

    # Padrão 2: Tabelas HTML
    tables = re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL)
    logger.info(f"Encontradas {len(tables)} tabelas")

    for t_idx, table in enumerate(tables):
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.DOTALL)
        logger.info(f"  Tabela {t_idx}: {len(rows)} linhas")

        for r_idx, row in enumerate(rows):
            # Extrai células
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
            if not cells:
                continue

            # Remove HTML e normaliza
            cell_text = [re.sub(r'<[^>]+>|&[a-z]+;', '', c).strip() for c in cells]
            cell_text = [c for c in cell_text if c]  # Remove vazios

            # Procura por MONTEPIO
            if any('MONTEPIO' in c.upper() for c in cell_text):
                caixa_name = ' '.join(cell_text[:3] if len(cell_text) >= 3 else cell_text).strip()

                # Procura por valor (padrão: dígitos com . ou , e €)
                valor_match = re.search(r'([\d.,]+)\s*€?', ' '.join(cell_text[-3:]))
                valor = valor_match.group(1) if valor_match else "?"

                results[caixa_name] = {
                    "valor": valor,
                    "cells": cell_text,
                    "table_idx": t_idx,
                    "row_idx": r_idx
                }
                logger.info(f"✅ Encontrado: {caixa_name} = {valor}")

    # Padrão 3: Busca livre por MONTEPIO
    if not results:
        lines_with_montepio = [
            line for line in html.split('\n')
            if 'MONTEPIO' in line.upper() and not line.strip().startswith('<!--')
        ]
        logger.info(f"Linhas com 'MONTEPIO': {len(lines_with_montepio)}")

        if lines_with_montepio:
            print(f"\n🔍 Linhas contendo 'MONTEPIO':")
            for line in lines_with_montepio[:5]:
                clean = re.sub(r'<[^>]+>', '', line).strip()
                print(f"   {clean[:100]}...")

    return results


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 100)
    print("EXTRAÇÃO DE DETALHES DO CAIXA - CAIXA ECONÔMICA MONTEPIO GERAL - CC")
    print("=" * 100)

    # Autenticação
    print("\n[1/3] Autenticando no SOMA...")
    if not auth.login():
        print("❌ Falha na autenticação")
        return 1
    print("✅ Autenticado com sucesso")

    # Acesso aos dados de caixas
    print("\n[2/3] Buscando dados de caixas...")
    html_data = fetch_gerenciar_caixas_data(http, settings)

    if not html_data:
        print("❌ Não foi possível obter dados de caixas de nenhuma fonte")
        print("\n💡 Sugestões:")
        print("   • Verifique as credenciais no .env")
        print("   • Verifique a URL base do SOMA em Settings")
        print("   • Acesse manualmente:")
        print(f"      https://verbodavida.info/IVV/index.php?mod=ivv&exec=caixas")
        return 1

    # Processamento
    print("\n[3/3] Processando dados...")
    results = extract_caixas_and_movs(html_data)

    # Exibição de resultados
    print("\n" + "=" * 100)
    print("RESULTADOS")
    print("=" * 100)

    if results:
        print(f"\n✅ Encontrados {len(results)} caixa(s):\n")
        for caixa_name, data in results.items():
            print(f"📊 {caixa_name}")
            print(f"   Valor: {data['valor']} €")
            print(f"   Localização: Tabela {data['table_idx']}, Linha {data['row_idx']}")
            print(f"   Células: {' | '.join(data['cells'][:5])}")
            print()
    else:
        print("\n⚠️  Nenhum caixa 'MONTEPIO' encontrado")
        print("\n💡 Para investigação manual, verifique as linhas com MONTEPIO acima")

    print("\n" + "=" * 100)
    print("📋 PRÓXIMOS PASSOS")
    print("=" * 100)
    print("""
1. Confirme se o valor exibido está correto acessando:
   https://verbodavida.info/IVV/index.php?mod=ivv&exec=caixas

2. Clique no caixa para ver a composição:
   • Doações/Dízimos/Ofertas (entrada)
   • Lanchonete/Café (entrada)
   • Livraria (entrada)
   • Menos: Repasses (saída)
   • Menos: Outras deduções

3. Se precisar extrair valores de componentes específicos, execute:
   python scripts/search_repasse_saidas.py
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
