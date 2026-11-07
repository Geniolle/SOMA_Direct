#!/usr/bin/env python3
"""
Investigação: O que o script encontrou na planilha?
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import TipoMovimento

def main():
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    print("=" * 120)
    print("🔍 INVESTIGAÇÃO: O Que o Script Encontrou")
    print("=" * 120)

    # Obter todas as linhas
    all_rows = sheets.get_all_rows(only_entrada_saida=False)

    # Contar Transferências
    transferencias = []
    for row in all_rows:
        if row.tipo == TipoMovimento.TRANSFERENCIA:
            status = (row.status or "").strip().upper()
            doc = (row.doc_soma or "").strip().upper()

            if (not doc or doc == "EM ERRO") and not status:
                transferencias.append(row)

    print(f"\n📊 ESTATÍSTICAS:")
    print(f"   Total de linhas na planilha: {len(all_rows)}")
    print(f"   Transferências EM ABERTO encontradas: {len(transferencias)}")
    print(f"\n   ⚠️  ISTO É MUITO DIFERENTE DO ESPERADO!")
    print(f"   Esperávamos: 2 Transferências")
    print(f"   Encontrou: {len(transferencias)} Transferências")

    print(f"\n📋 PRIMEIRAS 15 TRANSFERÊNCIAS EM ABERTO:")
    print("-" * 120)
    for i, row in enumerate(transferencias[:15], 1):
        desc = (row.descricao_soma or row.descricao or "?")[:50]
        data = row.data_mov or "?"
        val = row.importancia or "?"
        print(f"{i:3d}. Linha {row.row_number:4d}: {desc:50s} | Data: {data:12s} | Val: {str(val):10s}")

    if len(transferencias) > 15:
        print(f"\n   ... e mais {len(transferencias) - 15} transferências")

    print(f"\n" + "=" * 120)
    print(f"❓ ANÁLISE DO PROBLEMA:")
    print("=" * 120)

    print(f"\n1. ⚡ Volume Inesperado")
    print(f"   • Esperado: 2 Transferências")
    print(f"   • Encontrado: {len(transferencias)} Transferências")
    print(f"   • Aumento: {len(transferencias)/2:.0f}x maior que esperado!")

    print(f"\n2. 🔄 O que o Script Fez")
    print(f"   • Identificou as {len(transferencias)} Transferências em aberto")
    print(f"   • Tentou processar TODAS de uma vez")
    print(f"   • Fez chamada HTTPS para buscar cada uma no SOMA")
    print(f"   • Você pressionou Ctrl+C antes de completar")

    print(f"\n3. ⏳ Por que Travou?")
    print(f"   • Estava a fazer {len(transferencias)} chamadas HTTPS sequenciais")
    print(f"   • Cada chamada: POST para buscarTransferenciasCaixas.php")
    print(f"   • Sem limite/throttle → picos de carga no SOMA")
    print(f"   • Conexão SSL lenta ou SOMA congestionado")

    print(f"\n4. 🚨 Problema Real")
    print(f"   • O script NÃO tem limite de registos")
    print(f"   • Processa TODAS as linhas simultaneamente")
    print(f"   • Gera carga excessiva no SOMA")
    print(f"   • Impossível processar {len(transferencias)} Transferências assim")

    print(f"\n" + "=" * 120)
    print(f"💡 RECOMENDAÇÃO:")
    print("=" * 120)
    print(f"\n   Para processar {len(transferencias)} Transferências:")
    print(f"   1. Dividir em LOTES (ex: 10-20 por vez)")
    print(f"   2. Adicionar DELAY entre lotes")
    print(f"   3. Usar o AGENDADOR a cada 1 minuto (já faz isso)")
    print(f"   4. NÃO tentar processar todas de uma vez")

if __name__ == "__main__":
    main()
