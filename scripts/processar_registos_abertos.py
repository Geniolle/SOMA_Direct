#!/usr/bin/env python3
"""
Processa registos em aberto (Transferências e Outros).
Usa run_target_rows() em vez de run_pending() para contornar o filtro.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config.settings import Settings
from workflows.orchestrator import DirectOrchestrator
from services.sheets_service import GoogleSheetsService
from domain.models import is_entrada_ou_saida, TipoMovimento

def main():
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    orchestrator = DirectOrchestrator(settings)

    print("=" * 80)
    print("PROCESSAMENTO: Registos em Aberto (Transferências + Outros)")
    print("=" * 80)

    # Obter todas as linhas
    all_rows = sheets.get_all_rows(only_entrada_saida=False)

    # Filtrar registos abertos (sem DOC.SOMA e sem status)
    abertos = []
    for row in all_rows:
        status = (row.status or "").strip().upper()
        doc = (row.doc_soma or "").strip().upper()

        if (not doc or doc == "EM ERRO") and not status:
            abertos.append(row)

    if not abertos:
        print("✓ Nenhum registo em aberto encontrado!")
        return

    # Separar Transferências de Outros
    transferencias = [r for r in abertos if r.tipo == TipoMovimento.TRANSFERENCIA]
    outros = [r for r in abertos if r.tipo not in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA, TipoMovimento.TRANSFERENCIA)]

    print(f"\n📋 Registos a processar:")
    print(f"  • Transferências: {len(transferencias)}")
    if transferencias:
        for r in transferencias:
            print(f"    - Linha {r.row_number}: {r.descricao_soma}")

    print(f"  • Outros tipos: {len(outros)}")
    if outros:
        for r in outros:
            print(f"    - Linha {r.row_number}: Tipo={r.tipo.value}")

    # Processar Transferências (APENAS 1 POR EXECUÇÃO)
    if transferencias:
        print("\n" + "=" * 80)
        print(f"🔄 PROCESSANDO 1 TRANSFERÊNCIA (de {len(transferencias)} em aberto)...")
        print("=" * 80)

        # Processar apenas a primeira Transferência
        linhas_transferencias = [transferencias[0].row_number]
        print(f"\n📍 Processando: Linha {transferencias[0].row_number}")
        print(f"   Descrição: {transferencias[0].descricao_soma}")
        print(f"   Data: {transferencias[0].data_mov}")
        print(f"   Valor: {transferencias[0].importancia}")

        outcomes_trf = orchestrator.run_target_rows(linhas_transferencias, dry_run=False)

        sucesso_trf = sum(1 for o in outcomes_trf if o.success)
        erro_trf = len(outcomes_trf) - sucesso_trf

        print(f"\nResultado Transferências:")
        print(f"  ✓ Sucesso: {sucesso_trf}/{len(outcomes_trf)}")
        print(f"  ✗ Erro: {erro_trf}/{len(outcomes_trf)}")

        for outcome in outcomes_trf:
            status_icon = "✓" if outcome.success else "✗"
            print(f"    {status_icon} Linha {outcome.row_number}: {outcome.error_message or outcome.doc_id}")

    # Processar Outros
    if outros:
        print("\n" + "=" * 80)
        print(f"⚠️  {len(outros)} REGISTOS DO TIPO 'OUTRO'")
        print("=" * 80)
        print("\n⚠️  AVISO: Registos do tipo 'Outro' não são processados automaticamente.")
        print("   Estas linhas requerem:")
        print("   1. Definir o tipo correto (Entrada/Saída/Transferência)")
        print("   2. Depois executar este script novamente")
        print("\n   Linhas afetadas:")
        for r in outros:
            print(f"     - Linha {r.row_number}: Tipo='{r.tipo.value}'")

    print("\n" + "=" * 80)
    print("✓ Processamento concluído!")
    print("=" * 80)

if __name__ == "__main__":
    main()
