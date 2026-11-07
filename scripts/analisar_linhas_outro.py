#!/usr/bin/env python3
"""
Analisa as 6 linhas tipo "Outro" para decidir o que fazer.
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
    print("🔍 ANÁLISE: 6 Registos Tipo \"Outro\" (Linhas 192-197)")
    print("=" * 120)

    linhas_analise = [192, 193, 194, 195, 196, 197]

    for linha_num in linhas_analise:
        row = sheets.get_row(linha_num)
        if not row:
            print(f"\n❌ Linha {linha_num} não encontrada!")
            continue

        print(f"\n📋 LINHA {linha_num}")
        print("-" * 120)
        print(f"  TIPO:              {row.tipo.value if row.tipo else '???'}")
        print(f"  DATA MOV:          {row.data_mov or '(vazio)'}")
        print(f"  IMPORTÂNCIA:       {row.importancia or '(vazio)'}")

        desc = row.descricao_soma or row.descricao or "(vazio)"
        print(f"  DESCRIÇÃO:         {desc[:70]}")

        print(f"  CAIXA:             {row.caixa or '(vazio)'}")
        print(f"  PLANO DE CONTA:    {row.plano_conta or '(vazio)'}")
        print(f"  CENTRO DE CUSTO:   {row.centro_custo or '(vazio)'}")
        print(f"  FORMA PAGAMENTO:   {row.forma_pagamento or '(vazio)'}")
        print(f"  PROCESSO:          {row.processo or '(vazio)'}")
        print(f"  ID_INTERNO:        {row.id_interno or '(vazio)'}")
        print(f"  DOC.SOMA:          {row.doc_soma or '(vazio)'}")
        print(f"  STATUS:            {row.status or '(vazio)'}")
        print(f"  AUDITORIA:         {row.auditoria or '(vazio)'}")

        # Análise
        print(f"\n  🔎 ANÁLISE:")

        campos_vazio = []
        if not row.data_mov:
            campos_vazio.append('DATA')
        if not row.importancia:
            campos_vazio.append('IMPORTÂNCIA')
        if not row.caixa:
            campos_vazio.append('CAIXA')
        if not row.processo:
            campos_vazio.append('PROCESSO')
        if not row.id_interno:
            campos_vazio.append('ID_INTERNO')

        if campos_vazio:
            print(f"     ⚠️  Campos obrigatórios ausentes: {', '.join(campos_vazio)}")
        else:
            print(f"     ✓ Todos os campos obrigatórios presentes")

        # Verificar se pode ser Entrada ou Saída
        if row.importancia and row.data_mov:
            try:
                val = float(str(row.importancia).replace(',', '.'))
                if val > 0:
                    print(f"     → Poderia ser ENTRADA (valor positivo: {val})")
                elif val < 0:
                    print(f"     → Poderia ser SAÍDA (valor negativo: {val})")
            except:
                pass

    print("\n" + "=" * 120)
    print("📊 RESUMO")
    print("=" * 120)
    print("\n💡 Recomendação:")
    print("   Se estes registos têm dados completos → Corrigir tipo para Entrada/Saída")
    print("   Se estes registos estão incompletos → Deletar ou deixar bloqueados")
    print("   Se são testes/scratch → Deletar")

if __name__ == "__main__":
    main()
