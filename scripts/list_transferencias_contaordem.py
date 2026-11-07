#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
List all records from CONTAORDEM with tipo = "Transferência".
This is the first step to understand the data before building analysis logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Setup path
base_dir = Path(__file__).resolve().parents[1]
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import TipoMovimento, ContaOrdemRow


def main():
    """List all transferência records from CONTAORDEM."""
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    print("📊 Carregando sheet CONTAORDEM...\n")

    # Get all rows (including non-entrada/saida)
    all_rows = sheets.get_all_rows(only_entrada_saida=False)

    if not all_rows:
        print("❌ Nenhuma linha encontrada em CONTAORDEM.")
        return

    # Find transferência rows
    transferencias = [row for row in all_rows if row.tipo == TipoMovimento.TRANSFERENCIA]

    if not transferencias:
        print("✅ Nenhuma transferência encontrada em CONTAORDEM.")
        return

    print(f"✅ Encontradas {len(transferencias)} transferências:\n")
    print("-" * 150)

    for i, row in enumerate(transferencias, 1):
        print(f"\n[{i}] Linha {row.row_number}")
        print(f"    Data: {row.data_mov}")
        print(f"    Descrição: {row.descricao}")
        print(f"    Importância: {row.importancia}")
        print(f"    DOC. SOMA: {row.doc_soma}")
        print(f"    Tipo: {row.tipo}")
        print(f"    Caixa: {row.caixa}")
        print(f"    Caixa Saída: {row.caixa_saida}")
        print(f"    ID_INTERNO: {row.id_interno}")
        print(f"    Processo: {row.processo}")
        print(f"    Status: {row.status}")
        print(f"    Forma de Pagamento: {row.forma_pagamento}")
        print(f"    Descrição SOMA: {row.descricao_soma}")
        print(f"    DADOS DOC: {row.dados_doc}")

    print("\n" + "-" * 150)
    print(f"\n✅ Total: {len(transferencias)} transferências listadas.")

    # Check for TRANSFERENCIAS column in raw data
    if transferencias and transferencias[0].raw:
        headers = list(transferencias[0].raw.keys())
        if any('transferencia' in h.lower() for h in headers):
            print(f"   ✅ Campo TRANSFERENCIAS encontrado na sheet")
        else:
            print(f"   ⚠️  Campo TRANSFERENCIAS NÃO encontrado na sheet")
            print(f"   Colunas disponíveis: {headers}")


if __name__ == "__main__":
    main()
