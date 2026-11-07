#!/usr/bin/env python
"""
Extração de Repasses de Agosto 2026 do SOMA
Acessa: https://verbodavida.info/IVV/index.php?mod=ivv&exec=repasses
Data: 01/08/2026 a 31/08/2026
Objetivo: Listar os 5 repasses com valores exatos
"""

from __future__ import annotations

import re
import sys
import logging
from pathlib import Path
from html import unescape
from decimal import Decimal

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fetch_repasses_agosto")


def parse_valor(val_str):
    """Converte valor para Decimal."""
    try:
        s = str(val_str or "").strip()
        s = s.replace("€", "").replace("$", "").strip()
        s = s.replace(".", "").replace(",", ".")
        return Decimal(s)
    except:
        return Decimal("0")


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 130)
    print("EXTRAÇÃO: REPASSES DE AGOSTO 2026 DO SOMA")
    print("=" * 130)

    # Autenticação
    print("\n[1/3] Autenticando no SOMA...")
    if not auth.login():
        print("❌ Falha na autenticação")
        return 1
    print("✅ Autenticado")

    base_url = settings.site_base_url.rstrip("/")

    # Acessar página de repasses
    print("\n[2/3] Acessando página de repasses (agosto 2026)...")

    repasses_url = f"{base_url}/index.php?mod=ivv&exec=repasses"

    try:
        response = http.get(repasses_url, timeout=15)
        if response.status_code != 200:
            print(f"❌ Erro: Status {response.status_code}")
            return 1

        print(f"✅ Página acessada ({len(response.text)} bytes)")
    except Exception as e:
        print(f"❌ Erro de conexão: {e}")
        return 1

    # Procurar por tabelas e dados
    print("\n[3/3] Extraindo dados de repasses...")

    # Padrão 1: Procura por linhas com valores (padrão comum em dashboards)
    repasses_encontrados = []

    # Padrão: Procura por tabelas com dados de repasse
    tables = re.findall(r'<table[^>]*>(.*?)</table>', response.text, re.DOTALL)
    logger.info(f"Encontradas {len(tables)} tabelas")

    for table_idx, table_html in enumerate(tables):
        rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', table_html, re.DOTALL)

        for row_idx, row_html in enumerate(rows):
            cells = [unescape(re.sub(r'<[^>]+>', ' ', c)).strip()
                     for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row_html, re.DOTALL)]

            # Procura por linhas com valores monetários e descrições de repasse
            row_text = ' '.join(cells)

            if any(term in row_text.upper() for term in ['DÍZIMO', 'COVV', 'MISSÕES', 'NOVAS OBRAS', 'LIVRARIA', 'REPASSE']):
                # Extrai valor
                valor_match = re.search(r'([\d.,]+)\s*€?', row_text)
                if valor_match:
                    repasses_encontrados.append({
                        'table_idx': table_idx,
                        'row_idx': row_idx,
                        'cells': cells,
                        'text': row_text,
                        'valor_raw': valor_match.group(1)
                    })

    # Padrão 2: Procura por divs com class="repasse" ou similar
    div_repasses = re.findall(r'<div[^>]*class="[^"]*repasse[^"]*"[^>]*>(.*?)</div>', response.text, re.IGNORECASE | re.DOTALL)
    logger.info(f"Encontrados {len(div_repasses)} divs com classe 'repasse'")

    # Padrão 3: Procura por dados JSON/script com valores
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', response.text, re.DOTALL)
    logger.info(f"Encontrados {len(scripts)} blocos script")

    # Padrão 4: Busca por termos-chave com valores
    print("\n   Procurando por REPASSES E VALORES...")

    # Procura por "DÍZIMO", "COVV", etc. seguido de valor
    repasse_pattern = r'(?:DÍZIMO|COVV|MISSÕES|NOVAS OBRAS|LIVRARIA|repasse)[^€\n]*?([\d.,]+)\s*€?'
    matches = re.findall(repasse_pattern, response.text, re.IGNORECASE)

    logger.info(f"Encontrados {len(matches)} valores de repasse potenciais")

    # Resultado
    print("\n" + "=" * 130)
    print("RESULTADO - REPASSES DE AGOSTO 2026")
    print("=" * 130)

    if repasses_encontrados:
        print(f"\n✅ Encontrados {len(repasses_encontrados)} repasses em tabelas:\n")

        for i, rep in enumerate(repasses_encontrados, 1):
            valor = parse_valor(rep['valor_raw'])
            print(f"{i}. Valor: {rep['valor_raw']} € ({valor})")
            print(f"   Descrição: {rep['text'][:120]}")
            print()
    else:
        print("\n⚠️  Nenhum repasse encontrado em tabelas")

    if matches:
        print(f"\n📊 VALORES ENCONTRADOS (busca por padrão):\n")

        total = Decimal("0")
        for i, valor_str in enumerate(matches[:10], 1):  # Primeiros 10
            valor = parse_valor(valor_str)
            total += valor
            print(f"{i}. {valor_str} € (Decimal: {valor})")

        print(f"\n   TOTAL DOS PRIMEIROS {len(matches[:10])}: {total} €")

    # HTML bruto para análise
    print("\n" + "=" * 130)
    print("ANÁLISE DO HTML - PROCURANDO POR 'AGOSTO' OU '08/2026'")
    print("=" * 130)

    # Procura por referências a agosto
    agosto_sections = []
    lines = response.text.split('\n')

    for i, line in enumerate(lines):
        if any(term in line.upper() for term in ['AGOSTO', '08/2026', '31/08', '01/08']):
            agosto_sections.append((i, line[:150]))

    if agosto_sections:
        print(f"\n✅ Encontradas {len(agosto_sections)} referências a agosto:\n")

        for line_num, content in agosto_sections[:10]:
            print(f"   Linha {line_num}: {content}")
    else:
        print("\n⚠️  Nenhuma referência a 'AGOSTO' ou '08/2026' encontrada")
        print("   A página pode conter dados em formato diferente")

    # Salvando HTML para análise manual
    print("\n" + "=" * 130)
    print("NOTA: Salvando HTML completo para análise...")
    print("=" * 130)

    html_file = Path(__file__).parent.parent / "scratch_repasses_agosto.html"
    html_file.write_text(response.text, encoding='utf-8')
    print(f"\n✅ HTML salvo em: {html_file}")
    print(f"   Tamanho: {len(response.text)} bytes")

    print("\n" + "=" * 130)
    print("PRÓXIMOS PASSOS")
    print("=" * 130)
    print("""
Se os valores não foram extraídos automaticamente:

1. Você pode abrir o arquivo scratch_repasses_agosto.html no navegador
   e ver exatamente como os dados são apresentados

2. Alternativamente, acesse manualmente:
   https://verbodavida.info/IVV/index.php?mod=ivv&exec=repasses
   E filtre por:
   - Data Inicial: 01/08/2026
   - Data Final: 31/08/2026
   - Anote os 5 valores apresentados:
     * DÍZIMOS 10%: ___________
     * OFERTA COVV 2%: ________
     * OFERTA MISSÕES 1%: ______
     * OFERTA NOVAS OBRAS 2%: __
     * REPASSE LIVRARIA 3%: ____

3. Compare com o saldo real do caixa para descobrir a diferença
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
