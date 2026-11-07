#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Gravação em BATCH com respeito a quotas da Google Sheets API.

Valida todos os 151 registos de transferência contra o SOMA,
depois grava os resultados em lotes de 50 registos com delays entre batches.

Isto evita o erro "Write requests per minute per user" que ocorria com update_cell() individual.
"""

from __future__ import annotations

import sys
from pathlib import Path
import re
from datetime import datetime, timedelta
import time
from collections import defaultdict

# Setup path
base_dir = Path(__file__).resolve().parents[1]
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import TipoMovimento, clean_amount_for_comparison, clean_caixa
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from gspread.utils import ValueInputOption


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
    """Valida e grava em batch respeitando quotas."""

    print("\n" + "=" * 160)
    print("✅ GRAVAÇÃO EM BATCH: Validação + Gravação de Transferências")
    print("=" * 160 + "\n")

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    # 1. Carregar registos
    print("📊 1. Carregando registos de CONTAORDEM...\n")
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

    # 3. Validar cada registo
    print("📅 3. Validando registos...\n")

    datas_unicas = sorted(set(t.data_mov for t in transferencias))
    print(f"   {len(datas_unicas)} datas únicas encontradas\n")

    cache_soma = {}
    updates_list = []
    stats = {"validado": 0, "erro": 0}
    erros_unicos = defaultdict(int)

    from ronda_completa import parse_date

    for idx, registo in enumerate(transferencias, 1):
        # Buscar em cache ou fazer requisição
        data_obj = parse_date(registo.data_mov)
        data_inicio = (data_obj - timedelta(days=7)).strftime("%d/%m/%Y")
        data_fim = (data_obj + timedelta(days=7)).strftime("%d/%m/%Y")

        cache_key = (data_inicio, data_fim)

        if cache_key not in cache_soma:
            # Buscar no SOMA
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

        # Registar resultado
        if encontrado:
            status = "VALIDADO"
            soma_id = encontrado['id']
            diagnostico = ""
            stats["validado"] += 1
        else:
            # Procurar o erro mais próximo
            status = "ERRO"
            soma_id = ""
            diagnostico = "Não encontrado no SOMA"

            if soma_transfers:
                # Procurar qual campo não coincide
                for soma_t in soma_transfers:
                    data_ok = soma_t['data'] == registo.data_mov
                    valor_ok = clean_amount_for_comparison(soma_t['valor_saida']) == clean_amount_for_comparison(registo.importancia)
                    origem_ok = clean_caixa(soma_t['caixa_origem']) == clean_caixa(registo.caixa_saida)
                    destino_ok = clean_caixa(soma_t['caixa_destino']) == clean_caixa(registo.caixa)

                    # Se 3 dos 4 campos coincidem, é erro próximo
                    if sum([data_ok, valor_ok, origem_ok, destino_ok]) >= 3:
                        problemas = []
                        if not data_ok:
                            problemas.append(f"Data divergente ({soma_t['data']} vs {registo.data_mov})")
                        if not valor_ok:
                            problemas.append(f"Valor divergente ({soma_t['valor_saida']} vs {registo.importancia})")
                        if not origem_ok:
                            problemas.append(f"Origem divergente ({clean_caixa(soma_t['caixa_origem'])} vs {clean_caixa(registo.caixa_saida)})")
                        if not destino_ok:
                            problemas.append(f"Destino divergente ({clean_caixa(soma_t['caixa_destino'])} vs {clean_caixa(registo.caixa)})")

                        diagnostico = " | ".join(problemas)
                        break

            stats["erro"] += 1
            erros_unicos[diagnostico] += 1

        # Preparar para gravação em batch
        updates_list.append({
            "row": registo.row_number,
            "status": status,
            "soma_id": soma_id,
            "diagnostico": diagnostico,
            "id_interno": registo.id_interno,
        })

        # Mostrar progresso
        if idx <= 5 or idx == len(transferencias) or idx % 30 == 0:
            print(f"   [{idx:3d}/{len(transferencias)}] {status} {registo.id_interno}")

    # 4. Gravar na sheet em batches
    print("\n📝 4. Gravando na sheet em batches respeitando quotas...\n")

    try:
        spreadsheet = sheets.get_spreadsheet(settings.spreadsheet_url)
        worksheet = spreadsheet.worksheet(settings.sheet_contaordem)

        # Coluna AA = coluna 27
        col_number = 27
        col_letter = sheets._col_letter(col_number)

        print(f"   Coluna: AA (coluna {col_number})\n")

        # Configurar batch update
        batch_size = 50
        total_batches = (len(updates_list) + batch_size - 1) // batch_size
        total_updated = 0

        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min((batch_num + 1) * batch_size, len(updates_list))
            batch_chunk = updates_list[start_idx:end_idx]

            # Preparar dados para batch_update
            batch_data = []

            for update in batch_chunk:
                row_idx = update["row"]
                valor = f"{update['status']}"
                if update['soma_id']:
                    valor += f" | {update['soma_id']}"
                if update['diagnostico']:
                    valor += f" | {update['diagnostico']}"

                batch_data.append({
                    "range": f"AA{row_idx}",
                    "values": [[valor]]
                })

            # Executar batch_update
            try:
                worksheet.batch_update(batch_data, value_input_option=ValueInputOption.user_entered)
                total_updated += len(batch_chunk)
                print(f"   Batch {batch_num + 1}/{total_batches} ✅ ({len(batch_chunk)} registos)")

            except Exception as e:
                print(f"   Batch {batch_num + 1}/{total_batches} ❌ Erro: {e}")
                raise

            # Delay entre batches para não exceder quota
            if batch_num < total_batches - 1:
                time.sleep(3)  # 3 segundos entre batches

        print(f"\n✅ {total_updated} registos gravados com sucesso!\n")

    except Exception as e:
        print(f"❌ Erro durante gravação: {e}\n")
        import traceback
        traceback.print_exc()
        return

    # 5. Resumo final
    print("=" * 160)
    print("📊 RESUMO FINAL\n")

    print(f"Total de registos: {len(updates_list)}")
    print(f"✅ Validados: {stats['validado']} ({stats['validado']/len(updates_list)*100:.1f}%)")
    print(f"❌ Com Erros: {stats['erro']} ({stats['erro']/len(updates_list)*100:.1f}%)\n")

    if erros_unicos:
        print("Tipos de erro encontrados:\n")
        for erro, count in sorted(erros_unicos.items(), key=lambda x: x[1], reverse=True):
            print(f"  • {erro}")
            print(f"    Ocorrências: {count}\n")

    print(f"Coluna de gravação: TRANSFERENCIAS (coluna AA)")
    print(f"Formato: STATUS | SOMA_ID | DIAGNOSTICO")
    print(f"Método: batch_update() em lotes de {batch_size} com delays\n")

    print("=" * 160)
    print("🎉 ✅ VALIDAÇÃO E GRAVAÇÃO CONCLUÍDAS COM SUCESSO!")
    print("=" * 160 + "\n")


if __name__ == "__main__":
    main()
