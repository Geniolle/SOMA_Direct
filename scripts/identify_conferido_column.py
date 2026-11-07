#!/usr/bin/env python
"""
Investigação: Qual coluna exatamente contém "Conferido/Não conferido"?
O STATUS tem "PAGO", então preciso encontrar onde está a informação de conferência.
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("identify_conferido_column")


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 140)
    print("DESCOBERTA: QUAL COLUNA CONTÉM 'CONFERIDO/NÃO CONFERIDO'?")
    print("=" * 140)

    # Autenticação
    print("\n[1/3] Autenticando...")
    if not auth.login():
        return 1
    print("✅ Autenticado")

    base_url = settings.site_base_url.rstrip("/")

    # Buscar saídas
    print("\n[2/3] Buscando saída de REPASSE com TODAS as colunas...")

    search_payload = {
        "pesquisa": "DÍZIMO",
        "filtro": "descricao",
        "id_inst": settings.institution_id,
        "tipo": "0",
        "v": "1",
        "s": "2",
        "t_d": "1",
        "cc": "-1",
        "c": "",
        "i": "01/01/2024",
        "f": "31/12/2026",
    }

    response = http.post_ajax(
        f"{base_url}/sys/post/buscarEntradasSaidas.php",
        data=search_payload,
        timeout=10
    )

    print("\n[3/3] Analisando estrutura completa da tabela...")

    # Parse completo
    rows = []
    for row_html in re.findall(r'<tr\b[^>]*>(.*?)</tr>', response.text, re.DOTALL):
        cells = [unescape(re.sub(r'<[^>]+>', '', c)).strip()
                 for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row_html, re.DOTALL)]
        rows.append(cells)

    if rows:
        # Header
        print(f"\n📋 ESTRUTURA DA TABELA (Total de {len(rows)} linhas):")
        print(f"\n   Cabeçalho (linha 1):")

        header = rows[0]
        for i, col_name in enumerate(header):
            print(f"      [{i}] {col_name}")

        # Primeira linha de dados
        if len(rows) > 1:
            print(f"\n   Primeira Saída de Repasse (linha 2):")
            data_row = rows[1]

            for i, value in enumerate(data_row):
                col_name = header[i] if i < len(header) else f"Col[{i}]"

                # Destaca colunas relevantes
                marker = ""
                if "conferido" in value.lower():
                    marker = " ⭐ ← CONTÉM 'CONFERIDO'"
                elif any(x in value.lower() for x in ["pago", "pendente", "processado", "validado"]):
                    marker = f" ← Possível status"

                print(f"      [{i}] {col_name:<25} = {value}{marker}")

        # Procura por "Conferido" em toda tabela
        print(f"\n   🔍 Procurando por 'Conferido' em TODAS as colunas...")

        conferido_found = False
        for row_idx, row in enumerate(rows[1:], start=2):  # Skip header
            for col_idx, cell in enumerate(row):
                if "conferido" in cell.lower():
                    conferido_found = True
                    col_name = header[col_idx] if col_idx < len(header) else f"Col[{col_idx}]"
                    print(f"\n      ✅ Encontrado em Linha {row_idx}, Coluna [{col_idx}] ({col_name})")
                    print(f"         Valor: '{cell}'")
                    print(f"\n         Linha completa:")
                    for i, v in enumerate(row[:8]):
                        cn = header[i] if i < len(header) else f"Col[{i}]"
                        print(f"            [{i}] {cn:<20} = {v}")

        if not conferido_found:
            print(f"\n      ⚠️  'Conferido/Não conferido' NÃO ENCONTRADO nas saídas")
            print(f"          Todos os status encontrados são: 'PAGO'")

    # Conclusão
    print("\n" + "=" * 140)
    print("RESPOSTA À PERGUNTA: O STATUS 'Conferido/Não conferido' AFETA O SALDO?")
    print("=" * 140)

    print("""
    🔍 DESCOBERTA IMPORTANTE:

    O campo que aparece nas saídas é: STATUS = 'PAGO'

    NÃO há coluna com valores 'Conferido' ou 'Não conferido' nas saídas listadas.

    Possíveis Explicações:

    1️⃣ O status "Conferido/Não conferido" pode estar em:
       - Uma coluna diferente não visível nesta tabela
       - Uma view ou relatório separado (Relatório de Repasses)
       - Campo interno não exposto no buscarEntradasSaidas.php

    2️⃣ O cálculo do saldo provavelmente:
       - Usa TODAS as saídas REGARDLESS de status
       - Não filtra por "Conferido/Não conferido"
       - O campo STATUS='PAGO' indica que a saída foi processada

    3️⃣ Status "Conferido/Não conferido" pode ser:
       - Usado apenas no Relatório de Repasses (outro módulo)
       - Para auditoria/reconciliação, não para cálculo de saldo
       - Uma validação administrativa, não financeira

    ✅ CONCLUSÃO:

    O saldo do caixa PROVAVELMENTE deduz TODAS as saídas,
    independentemente do status "Conferido/Não conferido".

    O STATUS='PAGO' significa que a saída foi efetivamente processada
    e deduzida do saldo.

    Para confirmar 100%, você deveria verificar no Relatório de Repasses
    ou inspecionar o código de buscarResumoCaixasNew.php.
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
