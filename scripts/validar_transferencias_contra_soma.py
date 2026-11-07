#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Valida transferências de CONTAORDEM contra transferências do portal SOMA.

Fluxo:
1. Lista registos com tipo="Transferência" em CONTAORDEM
2. Para cada registo, busca no SOMA usando data + valor + caixas
3. Valida correspondência e gera resultado
4. Opcionalmente grava em T_TRANSFERENCIAS (tabela de auditoria)
"""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# Setup path
base_dir = Path(__file__).resolve().parents[1]
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import TipoMovimento, clean_amount_for_comparison, norm_basic
from workflows.orchestrator import DirectOrchestrator
from ronda_completa import (
    SomaTransfer,
    load_soma_transfers_interval,
    transfer_key,
    parse_date,
)


@dataclass
class TransferenciaValidationResult:
    """Resultado da validação de uma transferência."""
    id_interno: str
    data_mov: str
    descricao: str
    importancia: str
    caixa_origem: str
    caixa_destino: str

    # Resultados da busca em SOMA
    encontrado_soma: bool = False
    soma_transfer_id: Optional[str] = None
    soma_data: Optional[str] = None
    soma_valor_saida: Optional[str] = None
    soma_valor_entrada: Optional[str] = None

    # Diagnóstico
    status: str = "PENDENTE"  # PENDENTE, VALIDADO, DIVERGENCIA, NAO_ENCONTRADO
    diagnostico: str = ""

    def to_sheet_row(self) -> dict:
        """Converte resultado para linha de sheet."""
        return {
            "ID_INTERNO": self.id_interno,
            "DATA_MOV": self.data_mov,
            "DESCRICAO": self.descricao,
            "IMPORTANCIA": self.importancia,
            "CAIXA_ORIGEM": self.caixa_origem,
            "CAIXA_DESTINO": self.caixa_destino,
            "ENCONTRADO_SOMA": "SIM" if self.encontrado_soma else "NAO",
            "SOMA_ID": self.soma_transfer_id or "",
            "STATUS": self.status,
            "DIAGNOSTICO": self.diagnostico,
        }


def validar_transferencia(
    row,
    orchestrator: DirectOrchestrator,
    soma_transfers: dict[str, list[SomaTransfer]]
) -> TransferenciaValidationResult:
    """
    Valida um registo de transferência contra SOMA.

    Args:
        row: ContaOrdemRow com tipo = TRANSFERENCIA
        orchestrator: DirectOrchestrator autenticado
        soma_transfers: Dict com transferências por data
    """

    result = TransferenciaValidationResult(
        id_interno=row.id_interno,
        data_mov=row.data_mov,
        descricao=row.descricao,
        importancia=row.importancia,
        caixa_origem=row.caixa_saida,
        caixa_destino=row.caixa,
    )

    # Normalizar chave de busca
    target_key = transfer_key(
        row.data_mov,
        row.importancia,
        row.caixa_saida,
        row.caixa
    )

    # Procurar em SOMA por data
    key_date = row.data_mov  # Formato DD/MM/YYYY
    soma_list = soma_transfers.get(key_date, [])

    if not soma_list:
        result.status = "NAO_ENCONTRADO"
        result.diagnostico = f"Nenhuma transferência em {key_date} no SOMA"
        return result

    # Tentar encontrar correspondência
    for soma_transfer in soma_list:
        soma_key = transfer_key(
            soma_transfer.data,
            soma_transfer.valor_saida,
            soma_transfer.caixa_origem,
            soma_transfer.caixa_destino
        )

        if target_key == soma_key:
            result.encontrado_soma = True
            result.soma_transfer_id = soma_transfer.transfer_id
            result.soma_data = soma_transfer.data
            result.soma_valor_saida = soma_transfer.valor_saida
            result.soma_valor_entrada = soma_transfer.valor_entrada
            result.status = "VALIDADO"
            result.diagnostico = "Encontrada correspondência exata no SOMA"
            return result

    # Não encontrou correspondência exata
    result.status = "DIVERGENCIA"
    result.diagnostico = f"Não encontrada correspondência exata. {len(soma_list)} transferência(s) no dia em SOMA"

    return result


def main():
    """Valida todas as transferências contra SOMA."""
    print("\n" + "=" * 120)
    print("🔄 VALIDAÇÃO DE TRANSFERÊNCIAS - CONTAORDEM vs SOMA")
    print("=" * 120 + "\n")

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    orchestrator = DirectOrchestrator(settings)

    # 1. Carregar registos com tipo = Transferência
    print("📊 1. Carregando registos de CONTAORDEM com tipo='Transferência'...\n")
    all_rows = sheets.get_all_rows(only_entrada_saida=False)
    transferencias = [row for row in all_rows if row.tipo == TipoMovimento.TRANSFERENCIA]

    if not transferencias:
        print("✅ Nenhuma transferência encontrada.")
        return

    print(f"✅ Encontradas {len(transferencias)} transferências\n")

    # 2. Agrupar por datas para busca eficiente
    print("📅 2. Agrupando por datas para busca em SOMA...\n")
    datas_unicas = sorted(set(t.data_mov for t in transferencias))
    print(f"   Datas únicas: {len(datas_unicas)}")

    # 3. Buscar transferências do SOMA por período
    print(f"\n🌐 3. Buscando transferências no portal SOMA...\n")

    soma_transfers_by_date = {}

    # Buscar por períodos (para não exceder limites)
    for data_str in datas_unicas:
        try:
            data_obj = parse_date(data_str)
            # Buscar em período de 1 dia
            start = data_obj
            end = data_obj

            soma_transfers = load_soma_transfers_interval(orchestrator, start, end)
            soma_transfers_by_date[data_str] = soma_transfers

            if soma_transfers:
                print(f"   ✅ {data_str}: {len(soma_transfers)} transferência(s)")
            else:
                print(f"   ⚠️  {data_str}: nenhuma transferência")

        except Exception as e:
            print(f"   ❌ {data_str}: Erro - {e}")

    # 4. Validar cada registo
    print(f"\n📋 4. Validando {len(transferencias)} registos...\n")

    resultados = []
    stats = {"validado": 0, "divergencia": 0, "nao_encontrado": 0}

    for idx, row in enumerate(transferencias, 1):
        resultado = validar_transferencia(row, orchestrator, soma_transfers_by_date)
        resultados.append(resultado)

        # Atualizar stats
        if resultado.status == "VALIDADO":
            stats["validado"] += 1
            icon = "✅"
        elif resultado.status == "DIVERGENCIA":
            stats["divergencia"] += 1
            icon = "⚠️"
        else:
            stats["nao_encontrado"] += 1
            icon = "❌"

        if idx <= 5 or idx == len(transferencias):  # Mostrar primeiros 5 e último
            print(f"{icon} [{idx}/{len(transferencias)}] {resultado.id_interno} - {resultado.status}")
            if resultado.diagnostico:
                print(f"     {resultado.diagnostico}")
        elif idx == 6:
            print(f"   ... ({len(transferencias) - 5 - 1} mais) ...\n")

    # 5. Relatório
    print("\n" + "=" * 120)
    print("📊 RELATÓRIO FINAL:\n")
    print(f"   ✅ Validados: {stats['validado']}")
    print(f"   ⚠️  Divergências: {stats['divergencia']}")
    print(f"   ❌ Não Encontrados: {stats['nao_encontrado']}")
    print(f"   📈 Total: {len(transferencias)}")

    # Taxa de correspondência
    taxa_validacao = (stats["validado"] / len(transferencias) * 100) if transferencias else 0
    print(f"\n   Taxa de Correspondência: {taxa_validacao:.1f}%")

    print("\n" + "=" * 120)
    print("💡 Próximos Passos:")
    print("   1. Se a taxa de correspondência estiver aceitável, continue")
    print("   2. Para criar tabela T_TRANSFERENCIAS: use --create-table")
    print("   3. Para gravar resultados: use --apply")
    print("=" * 120 + "\n")


if __name__ == "__main__":
    main()
