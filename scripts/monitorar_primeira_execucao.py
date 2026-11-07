#!/usr/bin/env python3
"""
Monitora a primeira execução do agendador para validar Transferências.
Execute APÓS fazer deploy no servidor.
"""
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

import logging
from datetime import datetime, timedelta

# Configurar logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

def monitorar_agendador():
    """Monitora logs do agendador para validar processamento."""

    from services.sheets_service import GoogleSheetsService
    from config.settings import Settings

    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    print("=" * 100)
    print("🔍 MONITORAMENTO: Primeira Execução do Agendador")
    print("=" * 100)
    print("\n📋 Status Pré-Deploy:")
    print("-" * 100)

    # Obter status antes
    all_rows = sheets.get_all_rows(only_entrada_saida=False)

    abertos_antes = []
    for row in all_rows:
        status = (row.status or "").strip().upper()
        doc = (row.doc_soma or "").strip().upper()

        if (not doc or doc == "EM ERRO") and not status:
            abertos_antes.append(row)

    print(f"✓ Total de linhas na planilha: {len(all_rows)}")
    print(f"✓ Registos em aberto ANTES: {len(abertos_antes)}")

    if abertos_antes:
        print(f"\n  Detalhamento:")
        for r in abertos_antes[:10]:
            tipo = r.tipo.value if r.tipo else "???"
            print(f"    - Linha {r.row_number}: {tipo}")

    print("\n" + "=" * 100)
    print("⏳ AGUARDANDO PRIMEIRA EXECUÇÃO DO AGENDADOR")
    print("=" * 100)
    print("\n⚠️  Instruções:")
    print("   1. No servidor, execute: python agendador.py")
    print("   2. Deixe correr por 1-2 minutos")
    print("   3. Volte aqui e pressione ENTER para verificar resultados")
    print("\nEste script verificará automaticamente os logs...")

    # Aguardar 2 minutos
    print("\n⏱️  Aguardando 120 segundos para primeira execução...")
    for i in range(120, 0, -10):
        print(f"   {i}s restantes...", end='\r')
        time.sleep(10)

    print("\n" + "=" * 100)
    print("📊 VERIFICAÇÃO PÓS-EXECUÇÃO")
    print("=" * 100)

    # Obter status depois
    all_rows_depois = sheets.get_all_rows(only_entrada_saida=False)

    abertos_depois = []
    processados = 0
    for row in all_rows_depois:
        status = (row.status or "").strip().upper()
        doc = (row.doc_soma or "").strip().upper()
        auditoria = (row.auditoria or "").strip()

        if (not doc or doc == "EM ERRO") and not status:
            abertos_depois.append(row)
        elif doc and status.upper() == "VALIDADO":
            processados += 1

    print(f"\n✓ Registos em aberto ANTES: {len(abertos_antes)}")
    print(f"✓ Registos em aberto DEPOIS: {len(abertos_depois)}")
    print(f"✓ Registos processados: {processados}")

    # Verificar Transferências específicas
    print("\n" + "-" * 100)
    print("🔎 VERIFICAÇÃO ESPECÍFICA: Transferências")
    print("-" * 100)

    linha_2454 = sheets.get_row(2454)
    linha_3736 = sheets.get_row(3736)

    trf_verificadas = []

    if linha_2454:
        status_2454 = (linha_2454.status or "").strip().upper()
        doc_2454 = (linha_2454.doc_soma or "").strip()
        print(f"\n✓ Linha 2454 (20,00 EUR, 28/06/2025):")
        print(f"  Status: {status_2454 or '(vazio)'}")
        print(f"  DOC.SOMA: {doc_2454 or '(vazio)'}")

        if status_2454 == "VALIDADO" and doc_2454.startswith("TRF_"):
            print(f"  ✅ SUCESSO - Processada com DOC {doc_2454}")
            trf_verificadas.append(True)
        else:
            print(f"  ⚠️ Ainda não processada")
            trf_verificadas.append(False)

    if linha_3736:
        status_3736 = (linha_3736.status or "").strip().upper()
        doc_3736 = (linha_3736.doc_soma or "").strip()
        print(f"\n✓ Linha 3736 (330,00 EUR, 18/08/2024):")
        print(f"  Status: {status_3736 or '(vazio)'}")
        print(f"  DOC.SOMA: {doc_3736 or '(vazio)'}")

        if status_3736 == "VALIDADO" and doc_3736.startswith("TRF_"):
            print(f"  ✅ SUCESSO - Processada com DOC {doc_3736}")
            trf_verificadas.append(True)
        else:
            print(f"  ⚠️ Ainda não processada")
            trf_verificadas.append(False)

    print("\n" + "=" * 100)
    print("📈 RESULTADO FINAL")
    print("=" * 100)

    if all(trf_verificadas):
        print("\n✅ SUCESSO COMPLETO!")
        print("   • Ambas as Transferências foram processadas")
        print("   • Sistema funcionando normalmente")
        print("   • Agendador pode permanecer em produção")
    elif any(trf_verificadas):
        print("\n⚠️  SUCESSO PARCIAL")
        print("   • Uma ou mais Transferências foram processadas")
        print("   • Pode haver atraso na primeira execução")
        print("   • Aguarde próximo ciclo (1 minuto)")
    else:
        print("\n❌ NENHUMA TRANSFERÊNCIA FOI PROCESSADA")
        print("   • Verifique os logs do agendador:")
        print("     tail -f logs/soma_direct_*.log")
        print("   • Possíveis causas:")
        print("     1. Agendador não iniciado no servidor")
        print("     2. Erro de autenticação")
        print("     3. Problema na planilha Google Sheets")

    print("\n" + "=" * 100)
    print("🔍 Próximos passos:")
    print("-" * 100)
    print("   • Monitorar logs regularmente: tail -f logs/soma_direct_*.log")
    print("   • Verificar planilha para confirmação de processamento")
    print("   • Se houver erros, revisar logs e corrigir")
    print("=" * 100)

if __name__ == "__main__":
    try:
        monitorar_agendador()
    except Exception as e:
        logger.error(f"Erro ao monitorar: {e}", exc_info=True)
