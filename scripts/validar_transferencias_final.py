#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Validação FINAL de Transferências: CONTAORDEM vs SOMA

Fluxo correto:
1. Autentica no SOMA
2. Busca transferências do SOMA usando: i (data_inicio) e f (data_fim)
3. Extrai dados da tabela HTML
4. Compara com registos de CONTAORDEM
5. Gera relatório de validação
"""

from __future__ import annotations

import sys
from pathlib import Path
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Setup path
base_dir = Path(__file__).resolve().parents[1]
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import TipoMovimento, clean_amount_for_comparison, clean_caixa
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession


@dataclass
class SomaTransferenciaRow:
    """Uma transferência extraída da resposta HTML do SOMA."""
    transfer_id: str
    caixa_origem: str
    valor_saida: str
    caixa_destino: str
    valor_entrada: str
    data: str


def extrair_transferencias_soma(html_response: str) -> list[SomaTransferenciaRow]:
    """
    Extrai transferências da resposta HTML da API SOMA.

    Procura por: <tr>...<td>caixa_origem</td><td>valor_saida</td>...<td>data</td>...</tr>
    """
    transferencias = []

    # Procurar todas as linhas <tr> na tabela
    rows = re.findall(r'<tr>(.*?)</tr>', html_response, re.IGNORECASE | re.DOTALL)

    for row_html in rows:
        # Extrair células <td>
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.IGNORECASE | re.DOTALL)

        if len(cells) < 7:
            continue  # Pular linhas incompletas

        # Limpar HTML de cada célula
        clean_cells = []
        for cell in cells:
            # Remover HTML tags
            clean = re.sub(r'<[^>]+>', '', cell)
            clean = re.sub(r'&nbsp;', ' ', clean)
            clean = re.sub(r'&amp;', '&', clean)
            clean = clean.strip()
            clean_cells.append(clean)

        # Tentar extrair ID da transferência do atributo 'value' do checkbox
        id_match = re.search(r'<input[^>]*value="(\d+)"[^>]*>', row_html)
        transfer_id = id_match.group(1) if id_match else ""

        if not transfer_id:
            continue

        # Estrutura esperada:
        # [0] checkbox, [1] ações, [2] caixa_origem, [3] valor_saida,
        # [4] caixa_destino, [5] valor_entrada, [6] data

        if len(clean_cells) >= 7:
            caixa_origem = clean_cells[2].strip()
            valor_saida = clean_cells[3].strip()
            caixa_destino = clean_cells[4].strip()
            valor_entrada = clean_cells[5].strip()
            data = clean_cells[6].strip()

            # Pular se não tiver data
            if not data or not re.match(r'\d{2}/\d{2}/\d{4}', data):
                continue

            # Remover símbolo € dos valores
            valor_saida = re.sub(r'[€\s]', '', valor_saida)
            valor_entrada = re.sub(r'[€\s]', '', valor_entrada)

            transferencias.append(SomaTransferenciaRow(
                transfer_id=transfer_id,
                caixa_origem=caixa_origem,
                valor_saida=valor_saida,
                caixa_destino=caixa_destino,
                valor_entrada=valor_entrada,
                data=data,
            ))

    return transferencias


def transfer_key(data, valor, caixa_origem, caixa_destino) -> tuple:
    """Cria chave única para comparação de transferências."""
    return (
        data,
        clean_amount_for_comparison(valor),
        clean_caixa(caixa_origem),
        clean_caixa(caixa_destino),
    )


def main():
    """Validação final de transferências."""

    print("\n" + "=" * 140)
    print("✅ VALIDAÇÃO FINAL: CONTAORDEM vs SOMA")
    print("=" * 140 + "\n")

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    # 1. Carregar registos de CONTAORDEM
    print("📊 1. Carregando CONTAORDEM...\n")
    all_rows = sheets.get_all_rows(only_entrada_saida=False)
    transferencias_contaordem = [row for row in all_rows if row.tipo == TipoMovimento.TRANSFERENCIA]
    print(f"✅ {len(transferencias_contaordem)} transferências encontradas\n")

    # 2. Autenticar e buscar do SOMA
    print("🔐 2. Autenticando no SOMA...\n")
    http_session = ResilientSession(verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http_session)

    if not auth.login():
        print("❌ Falha na autenticação")
        return

    print("✅ Autenticado com sucesso\n")

    # 3. Buscar transferências do SOMA
    print("🌐 3. Buscando transferências no SOMA (26/08/2026 a 26/09/2026)...\n")

    url = f"{settings.site_base_url.rstrip('/')}/sys/post/buscarTransferenciasCaixas.php"

    response = http_session.session.post(
        url,
        data={
            "id_inst": "",  # Vazio = TODAS
            "i": "26/08/2026",
            "f": "26/09/2026",
        },
        headers={
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=settings.timeout_seconds
    )

    print(f"Status: {response.status_code}")
    print(f"Resposta: {len(response.text)} bytes\n")

    # 4. Extrair transferências
    print("📋 4. Extraindo transferências do HTML...\n")

    soma_transfers = extrair_transferencias_soma(response.text)
    print(f"✅ {len(soma_transfers)} transferência(s) extraída(s)\n")

    if soma_transfers:
        print("Amostra das primeiras 3:\n")
        for idx, t in enumerate(soma_transfers[:3], 1):
            print(f"  [{idx}] {t.data}: {t.caixa_origem} → {t.caixa_destino}")
            print(f"       {t.valor_saida} EUR | ID: {t.transfer_id}\n")

    # 5. Validar contra CONTAORDEM
    print("=" * 140)
    print("🔍 5. VALIDAÇÃO (CONTAORDEM vs SOMA)\n")

    stats = {"validadas": 0, "divergencias": 0, "nao_encontradas": 0}

    # Agrupar SOMA por chave para busca rápida
    soma_by_key = {}
    for st in soma_transfers:
        key = transfer_key(st.data, st.valor_saida, st.caixa_origem, st.caixa_destino)
        soma_by_key[key] = st

    # Validar cada transferência de CONTAORDEM
    print(f"Analisando {len(transferencias_contaordem)} registos de CONTAORDEM:\n")

    for idx, row in enumerate(transferencias_contaordem, 1):
        # Criar chave
        key = transfer_key(row.data_mov, row.importancia, row.caixa_saida, row.caixa)

        if key in soma_by_key:
            soma_t = soma_by_key[key]
            status = "✅ VALIDADO"
            stats["validadas"] += 1
        else:
            status = "❌ NÃO ENCONTRADO"
            stats["nao_encontradas"] += 1

        # Mostrar apenas alguns
        if idx <= 5 or idx == len(transferencias_contaordem):
            print(f"  [{idx}/{len(transferencias_contaordem)}] {status} | {row.id_interno} | {row.data_mov}")
        elif idx == 6:
            print(f"  ... ({len(transferencias_contaordem) - 5 - 1} mais) ...\n")

    # 6. Relatório
    print("\n" + "=" * 140)
    print("📊 RELATÓRIO FINAL:\n")
    print(f"  ✅ Validadas:       {stats['validadas']}")
    print(f"  ❌ Não encontradas: {stats['nao_encontradas']}")
    print(f"  📈 Total analisado: {len(transferencias_contaordem)}")

    taxa = (stats["validadas"] / len(transferencias_contaordem) * 100) if transferencias_contaordem else 0
    print(f"\n  Taxa de Correspondência: {taxa:.1f}%")

    print("\n" + "=" * 140)
    print("✅ Teste concluído com sucesso!")
    print("=" * 140 + "\n")


if __name__ == "__main__":
    main()
