#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Valida um registo REAL de CONTAORDEM contra SOMA.
Pega num registo, busca no SOMA pela data, e valida dados.
"""

from __future__ import annotations

import sys
from pathlib import Path
import re
from datetime import datetime, timedelta

# Setup path
base_dir = Path(__file__).resolve().parents[1]
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import TipoMovimento, clean_amount_for_comparison, clean_caixa
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession


def extrair_transferencias_soma(html_response: str):
    """Extrai transferências da resposta HTML do SOMA."""
    transferencias = []

    rows = re.findall(r'<tr>(.*?)</tr>', html_response, re.IGNORECASE | re.DOTALL)

    for row_html in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.IGNORECASE | re.DOTALL)

        if len(cells) < 7:
            continue

        clean_cells = []
        for cell in cells:
            clean = re.sub(r'<[^>]+>', '', cell)
            clean = re.sub(r'&nbsp;', ' ', clean)
            clean = re.sub(r'&amp;', '&', clean)
            clean = clean.strip()
            clean_cells.append(clean)

        id_match = re.search(r'<input[^>]*value="(\d+)"[^>]*>', row_html)
        transfer_id = id_match.group(1) if id_match else ""

        if not transfer_id or len(clean_cells) < 7:
            continue

        caixa_origem = clean_cells[2].strip()
        valor_saida = clean_cells[3].strip()
        caixa_destino = clean_cells[4].strip()
        valor_entrada = clean_cells[5].strip()
        data = clean_cells[6].strip()

        if not data or not re.match(r'\d{2}/\d{2}/\d{4}', data):
            continue

        valor_saida = re.sub(r'[€\s]', '', valor_saida)
        valor_entrada = re.sub(r'[€\s]', '', valor_entrada)

        transferencias.append({
            "id": transfer_id,
            "caixa_origem": caixa_origem,
            "valor_saida": valor_saida,
            "caixa_destino": caixa_destino,
            "valor_entrada": valor_entrada,
            "data": data,
        })

    return transferencias


def transfer_key(data, valor, caixa_origem, caixa_destino) -> tuple:
    """Cria chave única para comparação."""
    return (
        data,
        clean_amount_for_comparison(valor),
        clean_caixa(caixa_origem),
        clean_caixa(caixa_destino),
    )


def main():
    """Valida um registo real."""

    print("\n" + "=" * 140)
    print("🔍 VALIDAÇÃO DE REGISTO REAL: CONTAORDEM → SOMA")
    print("=" * 140 + "\n")

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    # 1. Carregar primeiro registo
    print("📊 1. Carregando um registo real de CONTAORDEM...\n")
    all_rows = sheets.get_all_rows(only_entrada_saida=False)
    transferencias = [row for row in all_rows if row.tipo == TipoMovimento.TRANSFERENCIA]

    if not transferencias:
        print("❌ Nenhuma transferência encontrada")
        return

    registo = transferencias[0]

    print(f"✅ Registo selecionado (Linha {registo.row_number}):\n")
    print(f"  ID_INTERNO:     {registo.id_interno}")
    print(f"  DATA MOV.:      {registo.data_mov}")
    print(f"  Descrição:      {registo.descricao}")
    print(f"  Importância:    {registo.importancia}")
    print(f"  Caixa Origem:   {registo.caixa_saida}")
    print(f"  Caixa Destino:  {registo.caixa}")
    print(f"  Status:         {registo.status}")
    print(f"  DADOS DOC:      {registo.dados_doc if registo.dados_doc else '(vazio)'}\n")

    # 2. Autenticar
    print("🔐 2. Autenticando no SOMA...\n")
    http_session = ResilientSession(verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http_session)

    if not auth.login():
        print("❌ Falha na autenticação")
        return

    print("✅ Autenticado\n")

    # 3. Buscar no SOMA com a data do registo
    print("🌐 3. Buscando no SOMA pela DATA MOV. do registo...\n")

    # Buscar com ±3 dias para ser seguro
    from ronda_completa import parse_date

    try:
        data_obj = parse_date(registo.data_mov)
        data_inicio = (data_obj - timedelta(days=3)).strftime("%d/%m/%Y")
        data_fim = (data_obj + timedelta(days=3)).strftime("%d/%m/%Y")

        print(f"  Data do registo: {registo.data_mov}")
        print(f"  Período busca:   {data_inicio} a {data_fim}\n")

    except Exception as e:
        print(f"❌ Erro ao parsear data: {e}")
        return

    url = f"{settings.site_base_url.rstrip('/')}/sys/post/buscarTransferenciasCaixas.php"

    response = http_session.session.post(
        url,
        data={
            "id_inst": "",
            "i": data_inicio,
            "f": data_fim,
        },
        headers={
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=settings.timeout_seconds
    )

    print(f"  Status HTTP: {response.status_code}")
    print(f"  Resposta: {len(response.text)} bytes\n")

    # 4. Extrair transferências do SOMA
    print("📋 4. Analisando resultado SOMA...\n")

    soma_transfers = extrair_transferencias_soma(response.text)
    print(f"  Transferências encontradas no período: {len(soma_transfers)}\n")

    if soma_transfers:
        print("  Lista de transferências neste período:\n")
        for idx, t in enumerate(soma_transfers, 1):
            print(f"    [{idx}] {t['data']}: {t['caixa_origem']} → {t['caixa_destino']}")
            print(f"         Saída: {t['valor_saida']} | Entrada: {t['valor_entrada']} | ID: {t['id']}\n")

    # 5. Validar
    print("=" * 140)
    print("🔎 5. VALIDAÇÃO DO REGISTO\n")

    # Criar chave do registo
    chave_registo = transfer_key(
        registo.data_mov,
        registo.importancia,
        registo.caixa_saida,
        registo.caixa
    )

    print(f"Chave de comparação (Registo):")
    print(f"  Data:          {registo.data_mov}")
    print(f"  Valor:         {clean_amount_for_comparison(registo.importancia)}")
    print(f"  Caixa origem:  {clean_caixa(registo.caixa_saida)}")
    print(f"  Caixa destino: {clean_caixa(registo.caixa)}\n")

    # Procurar correspondência
    encontrado = None
    for soma_t in soma_transfers:
        chave_soma = transfer_key(
            soma_t['data'],
            soma_t['valor_saida'],
            soma_t['caixa_origem'],
            soma_t['caixa_destino']
        )

        if chave_soma == chave_registo:
            encontrado = soma_t
            break

    # Resultado
    print("-" * 140)
    print("📊 RESULTADO DA VALIDAÇÃO:\n")

    if encontrado:
        print(f"✅ ENCONTRADO NO SOMA!\n")
        print(f"  ID SOMA:        {encontrado['id']}")
        print(f"  Data:           {encontrado['data']}")
        print(f"  Caixa Origem:   {encontrado['caixa_origem']}")
        print(f"  Valor Saída:    {encontrado['valor_saida']}")
        print(f"  Caixa Destino:  {encontrado['caixa_destino']}")
        print(f"  Valor Entrada:  {encontrado['valor_entrada']}")
        print(f"\n  ✅ STATUS: VALIDADO COM SUCESSO")

    else:
        print(f"❌ NÃO ENCONTRADO NO SOMA\n")

        # Mostrar o que foi encontrado de próximo
        if soma_transfers:
            print("Transferências próximas encontradas:\n")
            for soma_t in soma_transfers:
                # Comparar campo por campo
                data_match = soma_t['data'] == registo.data_mov
                valor_match = clean_amount_for_comparison(soma_t['valor_saida']) == clean_amount_for_comparison(registo.importancia)
                origem_match = clean_caixa(soma_t['caixa_origem']) == clean_caixa(registo.caixa_saida)
                destino_match = clean_caixa(soma_t['caixa_destino']) == clean_caixa(registo.caixa)

                match_count = sum([data_match, valor_match, origem_match, destino_match])

                print(f"  {soma_t['data']}: {soma_t['caixa_origem']} → {soma_t['caixa_destino']}")
                print(f"    Valor: {soma_t['valor_saida']} EUR")
                print(f"    Campos coincidentes: {match_count}/4")
                print(f"      • Data:     {'✅' if data_match else '❌'} ({soma_t['data']} vs {registo.data_mov})")
                print(f"      • Valor:    {'✅' if valor_match else '❌'} ({soma_t['valor_saida']} vs {registo.importancia})")
                print(f"      • Origem:   {'✅' if origem_match else '❌'} ({clean_caixa(soma_t['caixa_origem'])} vs {clean_caixa(registo.caixa_saida)})")
                print(f"      • Destino:  {'✅' if destino_match else '❌'} ({clean_caixa(soma_t['caixa_destino'])} vs {clean_caixa(registo.caixa)})\n")
        else:
            print("Nenhuma transferência encontrada no SOMA para este período.\n")

    print("=" * 140 + "\n")


if __name__ == "__main__":
    main()
