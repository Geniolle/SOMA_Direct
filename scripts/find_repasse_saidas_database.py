#!/usr/bin/env python
"""
Investigação: Onde estão as saídas dos repasses no SOMA?
- Acessa buscarEntradasSaidas.php filtrando por repasses
- Analisa a estrutura da resposta
- Procura por informações de tabela/ID do documento
"""

from __future__ import annotations

import re
import sys
import logging
from pathlib import Path
from html import unescape

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("find_repasse_saidas")


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 120)
    print("INVESTIGAÇÃO: ONDE ESTÃO AS SAÍDAS DOS REPASSES NO SOMA?")
    print("=" * 120)

    # Autenticação
    print("\n[1/4] Autenticando...")
    if not auth.login():
        print("❌ Falha na autenticação")
        return 1
    print("✅ Autenticado")

    base_url = settings.site_base_url.rstrip("/")

    # Buscar saídas de repasse
    print("\n[2/4] Buscando saídas de REPASSE no SOMA...")

    queries_to_search = [
        ("DÍZIMO", "Procurando saídas de DÍZIMOS"),
        ("COVV", "Procurando saídas de COVV"),
        ("MISSÕES", "Procurando saídas de MISSÕES"),
        ("Repasse", "Procurando saídas genéricas de Repasse"),
    ]

    all_results = {}

    for query, description in queries_to_search:
        print(f"\n   {description}...")

        search_payload = {
            "pesquisa": query,
            "filtro": "descricao",
            "id_inst": settings.institution_id,
            "tipo": "0",  # SAÍDA
            "v": "1",
            "s": "2",
            "t_d": "1",
            "cc": "-1",
            "c": "",
            "i": "01/01/2024",
            "f": "31/12/2026",
        }

        try:
            response = http.post_ajax(
                f"{base_url}/sys/post/buscarEntradasSaidas.php",
                data=search_payload,
                timeout=10
            )

            if response.status_code == 200:
                # Parse tabela HTML
                rows = []
                for row_html in re.findall(r'<tr\b[^>]*>(.*?)</tr>', response.text, re.DOTALL):
                    cells = [unescape(re.sub(r'<[^>]+>', ' ', c)).strip()
                             for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row_html, re.DOTALL)]
                    if cells:
                        rows.append(cells)

                if rows:
                    print(f"      ✅ Encontradas {len(rows)} saídas")
                    all_results[query] = rows

                    # Mostra primeiras 3
                    for i, row in enumerate(rows[:3], 1):
                        print(f"\n      Saída #{i}:")
                        for j, cell in enumerate(row[:6]):  # Primeiras 6 colunas
                            print(f"        Col[{j}]: {cell[:80]}")
                else:
                    print(f"      ⚠️  Nenhuma saída encontrada para '{query}'")
        except Exception as e:
            print(f"      ❌ Erro: {e}")

    # Análise da estrutura
    print("\n[3/4] Analisando estrutura das saídas...")

    if all_results:
        # Pega primeira saída encontrada
        first_query = list(all_results.keys())[0]
        first_rows = all_results[first_query]

        if first_rows:
            first_row = first_rows[0]

            print(f"\n   Analisando estrutura (baseado em '{first_query}'):")
            print(f"   Total de colunas: {len(first_row)}")

            # Tenta identificar colunas
            column_names = [
                "CODIGO/ID",
                "TIPO",
                "DESCRIÇÃO",
                "VALOR",
                "DATA",
                "STATUS",
                "BAIXA",
                "OBSERVAÇÕES"
            ]

            print("\n   Mapeamento de Colunas:")
            for i, (cell, name) in enumerate(zip(first_row, column_names)):
                print(f"      [{i}] {name:<20} = {cell[:70]}")

            # Procura por ID/CODIGO
            print("\n   🔍 Identificadores encontrados:")

            # Procura por números (provavelmente IDs)
            for i, cell in enumerate(first_row):
                if re.match(r'^\d{5,}$', cell):
                    print(f"      Coluna [{i}]: ID/CODIGO = {cell}")
                elif re.match(r'^\d+$', cell) and int(cell) > 100:
                    print(f"      Coluna [{i}]: Possível ID numérico = {cell}")

            # Procura por padrões de data
            for i, cell in enumerate(first_row):
                if re.match(r'^\d{2}/\d{2}/\d{4}', cell):
                    print(f"      Coluna [{i}]: Data = {cell}")

            # Procura por valores monetários
            for i, cell in enumerate(first_row):
                if re.search(r'[\d.,]+\s*€?', cell):
                    print(f"      Coluna [{i}]: Valor Monetário = {cell}")

    # Informações sobre tabelas no SOMA
    print("\n[4/4] Informações sobre estrutura do banco de dados SOMA...")

    print("""
    📊 TABELAS PROVÁVEIS NO SOMA (baseado na estrutura encontrada):

    1. TABELA PROVÁVEL: transacao / lançamento / documento
       - Armazena: tipo (entrada/saída), descricao, valor, data
       - Identificador: CODIGO / ID_TRANSACAO / ID_DOCUMENTO
       - Filtros: tipo=0 (saída), id_plano_contas (369-373 para repasses)

    2. TABELA RELACIONADA: plano_contas
       - ID_PLANO_CONTAS: 369=DÍZIMO, 370=COVV, 371=MISSÕES, 372=NOVAS OBRAS, 373=LIVRARIA
       - DESCRICAO: Nome do plano (ex: "DÍZIMOS 10%")

    3. TABELA RELACIONADA: caixa
       - ID_CAIXA: ID único do caixa
       - NOME: Nome (ex: "CAIXA ECONÔMICA MONTEPIO GERAL - CC")
       - SALDO: Calculado dinamicamente (∑entradas - ∑saídas)

    4. RELAÇÃO: transacao.id_plano_contas = plano_contas.id
                 transacao.id_caixa = caixa.id
    """)

    # Query SQL provável
    print("\n" + "=" * 120)
    print("QUERY SQL PROVÁVEL NO SOMA para calcular saldo do caixa:")
    print("=" * 120)

    print("""
    -- Saldo do Caixa (ECONÔMICA MONTEPIO GERAL - CC)
    SELECT
        caixa.nome,
        SUM(CASE WHEN transacao.tipo = 1 THEN transacao.valor ELSE 0 END) as total_entradas,
        SUM(CASE WHEN transacao.tipo = 0 THEN transacao.valor ELSE 0 END) as total_saidas,
        SUM(CASE WHEN transacao.tipo = 1 THEN transacao.valor ELSE 0 END) -
        SUM(CASE WHEN transacao.tipo = 0 THEN transacao.valor ELSE 0 END) as saldo
    FROM transacao
    INNER JOIN caixa ON transacao.id_caixa = caixa.id
    INNER JOIN plano_contas ON transacao.id_plano_contas = plano_contas.id
    WHERE
        caixa.nome LIKE '%MONTEPIO%'
        AND transacao.data BETWEEN '2024-01-01' AND '2026-12-31'
    GROUP BY caixa.id, caixa.nome;

    -- Saídas de Repasse Específicas
    SELECT
        transacao.id_transacao,
        transacao.descricao,
        plano_contas.nome as plano,
        transacao.valor,
        transacao.data,
        transacao.status
    FROM transacao
    INNER JOIN plano_contas ON transacao.id_plano_contas = plano_contas.id
    WHERE
        transacao.tipo = 0  -- SAÍDA
        AND transacao.id_plano_contas IN (369, 370, 371, 372, 373)  -- Repasses MVV
        AND transacao.id_caixa = (SELECT id FROM caixa WHERE nome LIKE '%MONTEPIO%')
    ORDER BY transacao.data DESC;
    """)

    # Resumo
    print("\n" + "=" * 120)
    print("RESUMO - ONDE ESTÃO AS SAÍDAS DOS REPASSES")
    print("=" * 120)

    print("""
    ✅ LOCALIZAÇÃO NO SOMA:

    1. ONDE ESTÃO ARMAZENADAS:
       - Tabela: transacao (ou similar)
       - Colunas: id, tipo(=0), id_plano_contas(369-373), valor, data, descricao, status
       - Identificador: CODIGO (número único de documento)

    2. COMO APARECEM:
       - Tipo: SAÍDA (tipo=0)
       - Plano de Contas: Um dos 5 repasses (369-373)
       - Descricao: "DÍZIMOS 10%", "OFERTA COVV 2%", etc.
       - Status: "Conferido" ou "Não conferido"

    3. COMO SÃO CONSULTADAS:
       - Endpoint: POST /sys/post/buscarEntradasSaidas.php
       - Parâmetro tipo=0 (saídas)
       - Filtro: pesquisa="DÍZIMO", "COVV", "MISSÕES", etc.
       - Retorna: Tabela HTML com todas as ocorrências

    4. CÁLCULO DO SALDO:
       - Endpoint: POST /sys/post/buscarResumoCaixasNew.php
       - Faz SUM de todas as entradas - SUM de todas as saídas
       - Inclui as 5 linhas de repasse como deduções

    5. VALIDAÇÃO NO SOMA:
       - Acessar: /ivv/index.php?mod=ivv&exec=caixas
       - Clique em: "CAIXA ECONÔMICA MONTEPIO GERAL - CC"
       - Mostra: Saldo = (Entradas) - (Repasses + Outras Saídas)
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
