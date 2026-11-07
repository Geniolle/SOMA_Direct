#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grava resultados na sheet com batch_update (respeitando quotas).
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
    """Grava resultados na sheet com batch_update."""

    print("\n" + "=" * 160)
    print("✅ GRAVANDO RESULTADOS NA SHEET (Batch Update)")
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
            diagnostico = "Não encontrado"

            if soma_transfers:
                for soma_t in soma_transfers:
                    data_ok = soma_t['data'] == registo.data_mov
                    valor_ok = clean_amount_for_comparison(soma_t['valor_saida']) == clean_amount_for_comparison(registo.importancia)
                    origem_ok = clean_caixa(soma_t['caixa_origem']) == clean_caixa(registo.caixa_saida)
                    destino_ok = clean_caixa(soma_t['caixa_destino']) == clean_caixa(registo.caixa)

                    if sum([data_ok, valor_ok, origem_ok, destino_ok]) >= 3:
                        problemas = []
                        if not data_ok:
                            problemas.append(f"Data:{soma_t['data']}")
                        if not valor_ok:
                            problemas.append(f"Val:{soma_t['valor_saida']}")
                        if not origem_ok:
                            problemas.append("Orig")
                        if not destino_ok:
                            problemas.append("Dest")

                        diagnostico = " ".join(problemas)
                        break

        updates_list.append({
            "row": registo.row_number,
            "status": status,
            "soma_id": soma_id,
            "diagnostico": diagnostico,
        })

        # Mostrar progresso
        if idx <= 5 or idx == len(transferencias) or idx % 30 == 0:
            print(f"   [{idx:3d}/{len(transferencias)}] {status} {registo.id_interno}")

    # 4. Gravar usando batch_update
    print("\n📝 4. Gravando na sheet com batch_update...\n")

    try:
        spreadsheet = sheets.get_spreadsheet(settings.spreadsheet_url)
        worksheet = spreadsheet.worksheet(settings.sheet_contaordem)

        # Coluna TRANSFERENCIAS
        col_transferencias = 26  # Coluna Z (0-indexed: 25)

        # Preparar batch update
        batch_data = []

        for update in updates_list:
            row_idx = update["row"]

            # Criar valor: STATUS | SOMA_ID | DIAGNOSTICO
            valor = f"{update['status']}"
            if update['soma_id']:
                valor += f" | {update['soma_id']}"
            if update['diagnostico']:
                valor += f" | {update['diagnostico']}"

            # Adicionar ao batch
            batch_data.append({
                "range": f"CONTAORDEM!{worksheet.cell(row_idx, col_transferencias + 1).address}",
                "values": [[valor]]
            })

        # Fazer batch update em lotes (100 por vez para respeitar quota)
        batch_size = 100
        total_updates = 0

        for i in range(0, len(batch_data), batch_size):
            batch_chunk = batch_data[i:i+batch_size]

            print(f"   Gravando lote {i//batch_size + 1}/{(len(batch_data) + batch_size - 1)//batch_size}...")

            try:
                spreadsheet.batch_update({
                    "data": batch_chunk,
                    "valueInputOption": "USER_ENTERED"
                })
                total_updates += len(batch_chunk)
            except Exception as e:
                print(f"   ⚠️  Erro no lote: {e}")
                # Continuar com o próximo lote
                pass

            # Delay entre lotes para respeitar quotas
            if i + batch_size < len(batch_data):
                time.sleep(2)

        print(f"\n✅ Gravação concluída! {total_updates} registos atualizados\n")

    except Exception as e:
        print(f"❌ Erro geral: {e}\n")
        import traceback
        traceback.print_exc()
        return

    # 5. Resumo
    print("=" * 160)
    print("📊 RESUMO\n")

    validados = sum(1 for u in updates_list if u["status"] == "VALIDADO")
    erros = sum(1 for u in updates_list if u["status"] == "ERRO")

    print(f"Total de registos: {len(updates_list)}")
    print(f"✅ Validados: {validados} ({validados/len(updates_list)*100:.1f}%)")
    print(f"❌ Com Erros: {erros} ({erros/len(updates_list)*100:.1f}%)\n")

    print(f"Coluna de gravação: TRANSFERENCIAS")
    print(f"Formato: STATUS | SOMA_ID | DIAGNOSTICO\n")

    print("=" * 160)
    print("✅ CONCLUÍDO COM SUCESSO!")
    print("=" * 160 + "\n")


if __name__ == "__main__":
    main()
