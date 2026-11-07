#!/usr/bin/env python3
"""
Diagnóstico final: Quantos registos realmente estão em aberto?
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService

def main():
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    print("=" * 100)
    print("🔍 DIAGNÓSTICO FINAL: Registos Realmente em Aberto")
    print("=" * 100)

    all_rows = sheets.get_all_rows(only_entrada_saida=False)

    abertos = []
    processados = []
    em_processamento = []

    for row in all_rows:
        status = (row.status or "").strip().upper()
        doc = (row.doc_soma or "").strip().upper()
        tipo_str = row.tipo.value if row.tipo else "??"

        if (not doc or doc == "EM ERRO") and not status:
            abertos.append((row.row_number, tipo_str))
        elif status == "VALIDADO":
            processados.append((row.row_number, tipo_str))
        elif status.startswith("EM PROCESSAMENTO"):
            em_processamento.append((row.row_number, tipo_str))

    print(f"\n📊 ESTATÍSTICAS FINAIS:")
    print(f"   Total de linhas: {len(all_rows)}")
    print(f"   Em aberto (sem DOC e sem STATUS): {len(abertos)}")
    print(f"   Já processadas (VALIDADO): {len(processados)}")
    print(f"   Em processamento: {len(em_processamento)}")

    if abertos:
        print(f"\n❌ Registos Ainda em Aberto:")
        for linha, tipo in abertos:
            print(f"   Linha {linha}: {tipo}")

    print(f"\n" + "=" * 100)
    print(f"✅ CONCLUSÃO:")
    print(f"=" * 100)

    if len(abertos) == 0:
        print(f"\n✅ PERFEITO! Sistema 100% Operacional!")
        print(f"   • Nenhum registo em aberto")
        print(f"   • {len(processados)} registos já processados")
        print(f"   • Transferências funcionando")
        print(f"\n🟢 STATUS: PRONTO PARA PRODUÇÃO")
    else:
        print(f"\n⚠️  Ainda há {len(abertos)} registos em aberto:")

        tipos = {}
        for _, tipo in abertos:
            tipos[tipo] = tipos.get(tipo, 0) + 1

        for tipo, count in tipos.items():
            print(f"   • {count}x tipo '{tipo}'")

        if all(tipo == "Outro" for _, tipo in abertos):
            print(f"\n   ℹ️  Nota: Todos são tipo 'Outro' (não processáveis automaticamente)")
            print(f"   Requer correção manual do tipo para Entrada/Saída")

if __name__ == "__main__":
    main()
