#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grava os resultados de validação de transferências na sheet CONTAORDEM.

Adiciona 3 colunas:
- VALIDACAO_STATUS: VALIDADO / ERRO
- SOMA_ID: ID da transferência no SOMA
- VALIDACAO_DIAGNOSTICO: Descrição do erro
"""

from __future__ import annotations

import sys
from pathlib import Path
import re
from datetime import datetime, timedelta
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

    # 3. Cache de buscas
    print("📅 3. Agrupando e buscando...\n")
    cache_soma = {}

    from ronda_completa import parse_date

    # 4. Validar e preparar updates
    print("🔄 4. Validando e preparando gravação...\n")

    resultados = []
    updates = []

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

        # Preparar update
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
                            problemas.append(f"Data divergente ({soma_t['data']} vs {registo.data_mov})")
                        if not valor_ok:
                            problemas.append(f"Valor divergente ({soma_t['valor_saida']} vs {registo.importancia})")
                        if not origem_ok:
                            problemas.append(f"Origem divergente")
                        if not destino_ok:
                            problemas.append(f"Destino divergente")

                        diagnostico = " | ".join(problemas)
                        break

            if not diagnostico:
                diagnostico = "Não encontrado no SOMA"

        updates.append({
            "row_number": registo.row_number,
            "id_interno": registo.id_interno,
            "status": status,
            "soma_id": soma_id,
            "diagnostico": diagnostico,
        })

        # Mostrar progresso
        if idx <= 5 or idx == len(transferencias) or idx % 30 == 0:
            print(f"   [{idx:3d}/{len(transferencias)}] {status} {registo.id_interno}")

    # 5. Gravar na sheet
    print("\n📝 5. Gravando na sheet CONTAORDEM...\n")

    # Obter headers da sheet para encontrar as colunas
    headers = sheets.get_headers()
    print(f"   Headers: {headers}\n")

    # Encontrar índices das colunas (ou criar se não existirem)
    col_validacao_status = None
    col_soma_id = None
    col_diagnostico = None

    for idx, h in enumerate(headers):
        h_lower = h.lower().strip()
        if "validacao" in h_lower and "status" in h_lower:
            col_validacao_status = idx
        elif "soma" in h_lower and "id" in h_lower:
            col_soma_id = idx
        elif "validacao" in h_lower and "diagnostico" in h_lower:
            col_diagnostico = idx

    # Se não encontrou, usar as próximas colunas vazias
    if col_validacao_status is None:
        col_validacao_status = len(headers)
    if col_soma_id is None:
        col_soma_id = len(headers) + 1
    if col_diagnostico is None:
        col_diagnostico = len(headers) + 2

    print(f"   Colunas para gravação:")
    print(f"     • VALIDACAO_STATUS: coluna {col_validacao_status + 1} ({chr(65 + col_validacao_status)})")
    print(f"     • SOMA_ID: coluna {col_soma_id + 1} ({chr(65 + col_soma_id)})")
    print(f"     • VALIDACAO_DIAGNOSTICO: coluna {col_diagnostico + 1} ({chr(65 + col_diagnostico)})\n")

    # Preparar batch de updates
    batch_updates = []

    for update in updates:
        row_idx = update["row_number"]

        # Update VALIDACAO_STATUS
        batch_updates.append({
            "range": f"CONTAORDEM!{chr(65 + col_validacao_status)}{row_idx}",
            "values": [[update["status"]]]
        })

        # Update SOMA_ID
        batch_updates.append({
            "range": f"CONTAORDEM!{chr(65 + col_soma_id)}{row_idx}",
            "values": [[update["soma_id"]]]
        })

        # Update VALIDACAO_DIAGNOSTICO
        batch_updates.append({
            "range": f"CONTAORDEM!{chr(65 + col_diagnostico)}{row_idx}",
            "values": [[update["diagnostico"]]]
        })

    # Gravar em batch
    print(f"   Gravando {len(batch_updates)} updates na sheet...\n")

    try:
        spreadsheet = sheets.get_spreadsheet(settings.spreadsheet_url)
        worksheet = spreadsheet.worksheet(settings.sheet_contaordem)

        # Batch update
        from gspread.utils import ValueInputOption

        body = {"data": batch_updates, "valueInputOption": ValueInputOption.user_entered}
        worksheet.batch_update(body)

        print(f"✅ Gravação concluída com sucesso!\n")

    except Exception as e:
        print(f"❌ Erro ao gravar: {e}\n")
        return

    # 6. Resumo final
    print("=" * 160)
    print("📊 RESUMO DA GRAVAÇÃO\n")

    validados = sum(1 for u in updates if u["status"] == "VALIDADO")
    erros = sum(1 for u in updates if u["status"] == "ERRO")

    print(f"Total de registos: {len(updates)}")
    print(f"✅ Validados: {validados} ({validados/len(updates)*100:.1f}%)")
    print(f"❌ Com Erros: {erros} ({erros/len(updates)*100:.1f}%)\n")

    print(f"Dados gravados nas colunas:")
    print(f"  • {chr(65 + col_validacao_status)}: VALIDACAO_STATUS")
    print(f"  • {chr(65 + col_soma_id)}: SOMA_ID")
    print(f"  • {chr(65 + col_diagnostico)}: VALIDACAO_DIAGNOSTICO")

    print("\n" + "=" * 160)
    print("✅ OPERAÇÃO CONCLUÍDA COM SUCESSO!")
    print("=" * 160 + "\n")


if __name__ == "__main__":
    main()
