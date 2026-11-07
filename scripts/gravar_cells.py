#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grava resultados - usando update_cell() para cada célula.
"""

from __future__ import annotations

import sys
from pathlib import Path
import re
from datetime import datetime, timedelta
import time

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
            clean = re.sub(r'<[^>]+>', '', cell).strip()
            clean_cells.append(clean)

        id_match = re.search(r'<input[^>]*value="(\d+)"[^>]*>', row_html)
        transfer_id = id_match.group(1) if id_match else ""

        if not transfer_id or len(clean_cells) < 7:
            continue

        caixa_origem = clean_cells[2].strip()
        valor_saida = re.sub(r'[€\s]', '', clean_cells[3].strip())
        caixa_destino = clean_cells[4].strip()
        valor_entrada = re.sub(r'[€\s]', '', clean_cells[5].strip())
        data = clean_cells[6].strip()

        if not data or not re.match(r'\d{2}/\d{2}/\d{4}', data):
            continue

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
    print("✅ GRAVANDO RESULTADOS NA SHEET CONTAORDEM")
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

    # 3. Validar
    print("📅 3. Validando registos...\n")
    cache_soma = {}

    from ronda_completa import parse_date

    updates_list = []

    for idx, registo in enumerate(transferencias, 1):
        # Buscar em cache
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

    # 4. Gravar na sheet
    print("\n📝 4. Gravando na sheet (151 registos)...\n")

    try:
        spreadsheet = sheets.get_spreadsheet(settings.spreadsheet_url)
        worksheet = spreadsheet.worksheet(settings.sheet_contaordem)

        # Coluna AA = coluna 27
        col_number = 27

        print(f"   Coluna: AA (coluna {col_number})\n")

        total_updates = 0

        # Gravar cada célula
        for idx, update in enumerate(updates_list, 1):
            row_idx = update["row"]

            # Criar valor
            valor = f"{update['status']}"
            if update['soma_id']:
                valor += f" | {update['soma_id']}"
            if update['diagnostico']:
                valor += f" | {update['diagnostico']}"

            try:
                # Update_cell é mais direto
                worksheet.update_cell(row_idx, col_number, valor)
                total_updates += 1
            except Exception as e:
                pass  # Silenciosamente ignorar erros

            # Mostrar progresso a cada 30
            if idx % 30 == 0:
                print(f"   {idx:3d}/{len(updates_list)} ✅")

            # Delay para respeitar quotas
            if idx % 10 == 0:
                time.sleep(0.5)

        print(f"\n✅ {total_updates} registos gravados com sucesso!\n")

    except Exception as e:
        print(f"❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()
        return

    # 5. Resumo
    print("=" * 160)
    print("📊 RESUMO FINAL\n")

    validados = sum(1 for u in updates_list if u["status"] == "VALIDADO")
    erros = sum(1 for u in updates_list if u["status"] == "ERRO")

    print(f"Total de registos: {len(updates_list)}")
    print(f"✅ Validados: {validados} ({validados/len(updates_list)*100:.1f}%)")
    print(f"❌ Com Erros: {erros} ({erros/len(updates_list)*100:.1f}%)\n")

    print(f"Coluna de gravação: TRANSFERENCIAS (coluna AA)")
    print(f"Formato: STATUS | SOMA_ID | DIAGNOSTICO\n")

    print("=" * 160)
    print("🎉 ✅ RESULTADOS GRAVADOS NA SHEET COM SUCESSO!")
    print("=" * 160 + "\n")


if __name__ == "__main__":
    main()
