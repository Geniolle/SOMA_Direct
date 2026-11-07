#!/usr/bin/env python3
"""
Diagnóstico de registos em aberto que não estão a ser processados.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import is_entrada_ou_saida, TipoMovimento

def main():
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    print("=" * 80)
    print("DIAGNÓSTICO: Registos em Aberto Não Processados")
    print("=" * 80)

    # Obter TODAS as linhas (incluindo Transferências)
    print("\n📋 Lendo planilha completa...")
    all_rows = sheets.get_all_rows(only_entrada_saida=False)
    print(f"Total de linhas na planilha: {len(all_rows)}")

    # Filtrar por estado "aberto" ou "pendente"
    print("\n🔍 Analisando estados de registos...\n")

    abertos = []
    em_processamento = []
    validados = []
    com_erro = []

    for row in all_rows:
        status = (row.status or "").strip().upper()
        doc = (row.doc_soma or "").strip().upper()
        auditoria = (row.auditoria or "").strip()

        # Classificar por status
        if status.startswith("EM PROCESSAMENTO"):
            em_processamento.append(row)
        elif status == "VALIDADO" and doc:
            validados.append(row)
        elif doc == "EM ERRO" or (not doc and not status):
            abertos.append(row)
        elif doc == "EM ERRO":
            com_erro.append(row)

    # Mostrar registos em aberto
    if abertos:
        print(f"❌ {len(abertos)} REGISTOS ABERTOS (sem DOC.SOMA e sem status):")
        print("-" * 80)
        for r in abertos:
            tipo_str = r.tipo.value if r.tipo else "???"
            entrada_saida = "✓ Entrada/Saída" if is_entrada_ou_saida(r.tipo) else "✗ NÃO é Entrada/Saída"
            print(f"  Linha {r.row_number:4d} | {tipo_str:15s} | {entrada_saida} | {r.descricao_soma[:40]}")
            print(f"             | DOC: '{r.doc_soma}' | STATUS: '{r.status}' | AUD: '{r.auditoria}'")

    if em_processamento:
        print(f"\n⏳ {len(em_processamento)} REGISTOS EM PROCESSAMENTO:")
        print("-" * 80)
        for r in em_processamento:
            tipo_str = r.tipo.value if r.tipo else "???"
            print(f"  Linha {r.row_number:4d} | {tipo_str:15s} | STATUS: {r.status}")

    if com_erro:
        print(f"\n⚠️  {len(com_erro)} REGISTOS COM DOC='EM ERRO':")
        print("-" * 80)
        for r in com_erro:
            tipo_str = r.tipo.value if r.tipo else "???"
            print(f"  Linha {r.row_number:4d} | {tipo_str:15s} | AUD: '{r.auditoria}' | DESC: {r.descricao_soma[:40]}")

    if validados:
        print(f"\n✅ {len(validados)} REGISTOS JÁ VALIDADOS:")
        print("-" * 80)
        print(f"   (Estes estão completos)")

    # Análise por tipo
    print("\n" + "=" * 80)
    print("📊 RESUMO POR TIPO:")
    print("-" * 80)

    transferencias = [r for r in abertos if r.tipo == TipoMovimento.TRANSFERENCIA]
    entradas = [r for r in abertos if r.tipo == TipoMovimento.ENTRADA]
    saidas = [r for r in abertos if r.tipo == TipoMovimento.SAIDA]
    outras = [r for r in abertos if r.tipo not in (TipoMovimento.TRANSFERENCIA, TipoMovimento.ENTRADA, TipoMovimento.SAIDA)]

    print(f"  Transferências:  {len(transferencias):3d} (✗ EXCLUÍDAS de run_pending)")
    print(f"  Entradas:        {len(entradas):3d} (✓ Devem ser processadas)")
    print(f"  Saídas:          {len(saidas):3d} (✓ Devem ser processadas)")
    print(f"  Outros:          {len(outras):3d}")

    # Filtro que run_pending() aplica
    print("\n" + "=" * 80)
    print("🚫 FILTRO DE run_pending():")
    print("-" * 80)
    print("  1. only_entrada_saida=True  → Exclui Transferências, Cartão, MVV, Outro")
    print("  2. doc_soma vazio OU 'EM ERRO'")
    print("  3. status NÃO 'EM PROCESSAMENTO' (ou stale)")

    processavel_por_run_pending = [
        r for r in abertos
        if is_entrada_ou_saida(r.tipo)
    ]

    print(f"\n  → Registos que run_pending() PROCESSARIA: {len(processavel_por_run_pending)}")
    if processavel_por_run_pending:
        for r in processavel_por_run_pending:
            print(f"    Linha {r.row_number}: {r.tipo.value} | {r.descricao_soma[:50]}")

    # Problemas detectados
    print("\n" + "=" * 80)
    print("🔧 POSSÍVEIS PROBLEMAS:")
    print("-" * 80)

    problemas = []

    if transferencias and len(transferencias) > 0:
        problemas.append(
            f"• {len(transferencias)} Transferências em aberto:\n"
            "  run_pending() EXCLUI Transferências (only_entrada_saida=True)\n"
            "  → USE: orchestrator.run_target_rows([linhas]) com tipos Transferência"
        )

    if not processavel_por_run_pending and len(abertos) > 0:
        problemas.append(
            f"• {len(abertos)} registos abertos, mas NENHUM é Entrada/Saída\n"
            "  → run_pending() não vai processar nada\n"
            "  → USE: run_target_rows() para processar explicitamente"
        )

    if em_processamento:
        problemas.append(
            f"• {len(em_processamento)} registos BLOQUEADOS em 'EM PROCESSAMENTO'\n"
            "  → Verificar se um processo anterior travou\n"
            "  → Se stale (> claim_stale_seconds), será automaticamente libertado"
        )

    if not problemas:
        problemas.append("✓ Nenhum problema óbvio detectado")

    for p in problemas:
        print(p)
        print()

    print("=" * 80)

if __name__ == "__main__":
    main()
