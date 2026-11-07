#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grava os resultados de validação de transferências na sheet CONTAORDEM.
Versão corrigida com método correto de batch_update.
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
    """Grava resultados na sheet."""

    print("\n" + "=" * 160)
    print("✅ GRAVANDO RESULTADOS NA SHEET CONTAORDEM (Versão Corrigida)")
    print("=" * 160 + "\n")

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    # 1. Carregar registos
    print("📊 1. Carregando registos...\n")
    all_rows = sheets.get_all_rows(only_entrada_saida=False)
    transferencias = [row for row in all_rows if row.tipo == TipoMovimento.TRANSFERENCIA]
    print(f"✅ {len(transferencias)} transferências carregadas\n")

    # 2. Autenticar
    print("🔐 2. Autenticando no SOMA...\n")
    http_session = ResilientSession(verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http_session)

    if not auth.login():
        print("❌ Falha na autenticação")
        return

    print("✅ Autenticado\n")

    # 3. Cache de buscas
    print("📅 3. Validando registos...\n")
    cache_soma = {}

    from ronda_completa import parse_date

    # 4. Validar
    updates_list = []

    for idx, registo in enumerate(transferencias, 1):
        # Buscar em cache ou fazer requisição
        data_obj = parse_date(registo.data_mov)
        data_inicio = (data_obj - timedelta(days=7)).strftime("%d/%m/%Y")
        data_fim = (data_obj + timedelta(days=7)).strftime("%d/%m/%Y")

        cache_key = (data_inicio, data_fim)

        if cache_key not in cache_soma:
            url = f"{settings.site_base_url.rstrip('/')}/sys/post/buscarTransferenciasCaixas.php"
            response = http_session.session.post(
                url,
                data={"id_inst": "", "i": data_inicio, "f": data_fim},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=settings.timeout_seconds
            )
            cache_soma[cache_key] = extrair_transferencias_soma(response.text)

        soma_transfers = cache_soma[cache_key]

        # Procurar correspondência
        chave_registo = transfer_key(
            registo.data_mov,
            registo.importancia,
            registo.caixa_saida,
            registo.caixa
        )

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

        # Preparar resultado
        if encontrado:
            status = "VALIDADO"
            soma_id = encontrado['id']
            diagnostico = ""
        else:
            status = "ERRO"
            soma_id = ""

            if soma_transfers:
                for soma_t in soma_transfers:
                    data_ok = soma_t['data'] == registo.data_mov
                    valor_ok = clean_amount_for_comparison(soma_t['valor_saida']) == clean_amount_for_comparison(registo.importancia)
                    origem_ok = clean_caixa(soma_t['caixa_origem']) == clean_caixa(registo.caixa_saida)
                    destino_ok = clean_caixa(soma_t['caixa_destino']) == clean_caixa(registo.caixa)

                    if sum([data_ok, valor_ok, origem_ok, destino_ok]) >= 3:
                        problemas = []
                        if not data_ok:
                            problemas.append(f"Data: {soma_t['data']} vs {registo.data_mov}")
                        if not valor_ok:
                            problemas.append(f"Valor: {soma_t['valor_saida']} vs {registo.importancia}")
                        if not origem_ok:
                            problemas.append(f"Origem")
                        if not destino_ok:
                            problemas.append(f"Destino")

                        diagnostico = " | ".join(problemas)
                        break

            if not diagnostico:
                diagnostico = "Não encontrado"

        updates_list.append({
            "row": registo.row_number,
            "status": status,
            "soma_id": soma_id,
            "diagnostico": diagnostico,
        })

        # Mostrar progresso
        if idx <= 5 or idx == len(transferencias) or idx % 30 == 0:
            print(f"   [{idx:3d}/{len(transferencias)}] {status} {registo.id_interno}")

    # 5. Gravar na sheet
    print("\n📝 5. Gravando na sheet CONTAORDEM...\n")

    # Obter headers
    headers = sheets.get_headers()
    print(f"   Headers: {len(headers)} colunas\n")

    # Procurar coluna TRANSFERENCIAS (já existe!)
    col_transferencias = None
    for idx, h in enumerate(headers):
        if h.lower().strip() == "transferencias":
            col_transferencias = idx
            break

    if col_transferencias is None:
        # Se não existir, usar próxima coluna vazia
        col_transferencias = len(headers)
        print(f"   ⚠️  Coluna TRANSFERENCIAS não encontrada. Usando coluna {col_transferencias + 1}\n")
    else:
        print(f"   ✅ Coluna TRANSFERENCIAS encontrada (coluna {col_transferencias + 1})\n")

    # Gravar usando update_cells individual (mais seguro)
    print(f"   Gravando {len(updates_list)} registos...\n")

    try:
        spreadsheet = sheets.get_spreadsheet(settings.spreadsheet_url)
        worksheet = spreadsheet.worksheet(settings.sheet_contaordem)

        # Gravar cada registo
        for update in updates_list:
            row_idx = update["row"]

            # Criar valor: STATUS | SOMA_ID | DIAGNOSTICO
            valor = f"{update['status']}"
            if update['soma_id']:
                valor += f" | {update['soma_id']}"
            if update['diagnostico']:
                valor += f" | {update['diagnostico']}"

            # Gravar na coluna TRANSFERENCIAS
            cell = worksheet.cell(row_idx, col_transferencias + 1)
            cell.value = valor
            worksheet.update_cell(row_idx, col_transferencias + 1, valor)

        print(f"✅ Gravação concluída com sucesso!\n")

    except Exception as e:
        print(f"❌ Erro ao gravar: {e}\n")
        import traceback
        traceback.print_exc()
        return

    # 6. Resumo
    print("=" * 160)
    print("📊 RESUMO DA GRAVAÇÃO\n")

    validados = sum(1 for u in updates_list if u["status"] == "VALIDADO")
    erros = sum(1 for u in updates_list if u["status"] == "ERRO")

    print(f"Total de registos: {len(updates_list)}")
    print(f"✅ Validados: {validados} ({validados/len(updates_list)*100:.1f}%)")
    print(f"❌ Com Erros: {erros} ({erros/len(updates_list)*100:.1f}%)\n")

    print(f"Coluna de gravação: TRANSFERENCIAS (coluna {col_transferencias + 1})")
    print(f"Formato: STATUS | SOMA_ID | DIAGNOSTICO\n")

    print(f"Exemplos:")
    for u in updates_list[:3]:
        valor = f"{u['status']}"
        if u['soma_id']:
            valor += f" | {u['soma_id']}"
        if u['diagnostico']:
            valor += f" | {u['diagnostico']}"
        print(f"  • Linha {u['row']}: {valor}\n")

    print("=" * 160)
    print("✅ OPERAÇÃO CONCLUÍDA COM SUCESSO!")
    print("=" * 160 + "\n")


if __name__ == "__main__":
    main()
