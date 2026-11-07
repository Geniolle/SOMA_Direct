#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Analyze a single transferência record from CONTAORDEM.
This simulates the validation logic before applying to all records.

Similar to repasse_service.py but for TRANSFERENCIAS column.
"""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Setup path
base_dir = Path(__file__).resolve().parents[1]
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import TipoMovimento
from workflows.orchestrator import DirectOrchestrator
from ronda_completa import (
    SomaTransfer,
    load_soma_transfers_interval,
    transfer_key,
    parse_date,
)


@dataclass
class TransferenciaValidationRecord:
    """Validation result for a single transferência."""
    id_validacao: str
    data_execucao: str
    id_interno: str
    data_mov: str
    descricao: str
    importancia: str
    caixa: str
    caixa_saida: str
    status_contaordem: str

    # Validação contra SOMA
    encontrado_soma: bool = False
    doc_soma_id: Optional[str] = None
    status_validacao: str = "PENDENTE"
    diagnostico: str = ""
    observacao: str = ""


def get_first_transferencia_record():
    """Get the first transferência record for simulation."""
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    all_rows = sheets.get_all_rows(only_entrada_saida=False)
    transferencias = [row for row in all_rows if row.tipo == TipoMovimento.TRANSFERENCIA]

    if not transferencias:
        return None

    return transferencias[0]  # First record for simulation


def validate_transferencia_against_soma(
    orchestrator: DirectOrchestrator,
    row,
    soma_transfers: list[SomaTransfer]
) -> TransferenciaValidationRecord:
    """
    Validate a transferência record against SOMA transfers.

    Steps:
    1. Extract caixas and value from CONTAORDEM row
    2. Search for matching transfer in SOMA
    3. Generate validation result
    """

    record = TransferenciaValidationRecord(
        id_validacao=f"TRANSF_{row.id_interno}_{datetime.now().isoformat()}",
        data_execucao=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        id_interno=row.id_interno,
        data_mov=row.data_mov,
        descricao=row.descricao,
        importancia=row.importancia,
        caixa=row.caixa,
        caixa_saida=row.caixa_saida,
        status_contaordem=row.status,
    )

    if not soma_transfers:
        record.status_validacao = "NAO_ENCONTRADO_SOMA"
        record.diagnostico = "Nenhuma transferência no SOMA no período analisado"
        return record

    # Try to match with SOMA transfer
    target_key = transfer_key(
        row.data_mov,
        row.importancia,
        row.caixa_saida,
        row.caixa
    )

    for soma_transfer in soma_transfers:
        soma_key = transfer_key(
            soma_transfer.data,
            soma_transfer.valor_saida,
            soma_transfer.caixa_origem,
            soma_transfer.caixa_destino
        )

        if target_key == soma_key:
            record.encontrado_soma = True
            record.doc_soma_id = soma_transfer.transfer_id
            record.status_validacao = "VALIDADO"
            record.diagnostico = "Transferência encontrada no SOMA com dados coincidentes"
            record.observacao = f"SOMA Transfer ID: {soma_transfer.transfer_id}"
            return record

    # Not found with exact match
    record.status_validacao = "DIVERGENCIA"
    record.diagnostico = "Transferência não encontrada com correspondência exata no SOMA"
    record.observacao = "Pode haver divergência em data, valor ou caixas"

    return record


def main():
    """Analyze first transferência record as simulation."""
    print("\n" + "=" * 100)
    print("🔍 SIMULAÇÃO: Análise de Transferência")
    print("=" * 100 + "\n")

    # Get first record
    row = get_first_transferencia_record()

    if not row:
        print("❌ Nenhuma transferência encontrada para simular.")
        return

    print("📋 Registo Selecionado para Simulação:")
    print(f"   Linha: {row.row_number}")
    print(f"   Data: {row.data_mov}")
    print(f"   Descrição: {row.descricao}")
    print(f"   Importância: {row.importancia}")
    print(f"   ID_INTERNO: {row.id_interno}")
    print(f"   Caixa: {row.caixa}")
    print(f"   Caixa Saída: {row.caixa_saida}")
    print(f"   Status: {row.status}")

    print("\n" + "-" * 100)
    print("🔎 Buscando transferências no portal SOMA...\n")

    try:
        settings = Settings.from_env()
        orchestrator = DirectOrchestrator(settings)

        # Parse date and get interval for SOMA query
        # For simulation, search +/- 7 days
        try:
            from ronda_completa import parse_date
            data_obj = parse_date(row.data_mov)
            from datetime import timedelta
            start_date = data_obj - timedelta(days=7)
            end_date = data_obj + timedelta(days=7)

            print(f"   Período de busca: {start_date.strftime('%d/%m/%Y')} a {end_date.strftime('%d/%m/%Y')}")

            soma_transfers = load_soma_transfers_interval(orchestrator, start_date, end_date)
            print(f"   ✅ Encontradas {len(soma_transfers)} transferências no SOMA\n")
        except Exception as e:
            print(f"   ⚠️  Erro ao buscar SOMA: {e}\n")
            soma_transfers = []

        # Validate
        result = validate_transferencia_against_soma(orchestrator, row, soma_transfers)

        print("-" * 100)
        print("📊 Resultado da Validação:\n")
        print(f"   ID Validação: {result.id_validacao}")
        print(f"   Data Execução: {result.data_execucao}")
        print(f"   ID_INTERNO: {result.id_interno}")
        print(f"   Data do Movimento: {result.data_mov}")
        print(f"   Importância: {result.importancia}")
        print(f"   Status Validação: {result.status_validacao}")
        print(f"   Diagnóstico: {result.diagnostico}")
        print(f"   Observação: {result.observacao}")

        if result.encontrado_soma:
            print(f"   ✅ SOMA Transfer ID: {result.doc_soma_id}")
        else:
            print(f"   ❌ Não encontrado em SOMA")

        print("\n" + "=" * 100)
        print("💡 Próximos Passos:")
        print("   1. Validar este resultado")
        print("   2. Se válido, aplicar lógica para os 150 restantes")
        print("   3. Opção: --apply para gravar resultados na sheet T_TRANSFERENCIAS")
        print("=" * 100 + "\n")

    except Exception as e:
        print(f"❌ Erro na simulação: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
