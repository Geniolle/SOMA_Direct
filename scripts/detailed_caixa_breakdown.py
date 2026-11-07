#!/usr/bin/env python
"""
Quebra detalhada da composição do saldo do caixa CAIXA ECONÔMICA MONTEPIO GERAL - CC.
Mostra exatamente quais processos contribuem para o valor final.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import TipoMovimento


def parse_valor(val_str: str) -> float:
    """Converte string de valor para float."""
    try:
        s = str(val_str or "").strip()
        s = s.replace("€", "").replace("$", "").strip()
        s = s.replace(".", "").replace(",", ".")  # Formato europeu
        return float(s)
    except:
        return 0.0


def main():
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    print("=" * 110)
    print("QUEBRA DETALHADA - COMPOSIÇÃO DO SALDO DO CAIXA MONTEPIO")
    print("=" * 110)

    # Lê dados
    print("\n[Lendo dados...]")
    all_rows = sheets.get_all_rows(only_entrada_saida=False)

    # Filtra apenas MONTEPIO
    montepio_rows = []
    for row in all_rows:
        caixa_clean = re.sub(r'\[.*?\]', '', row.caixa).strip().lower()
        if 'montepio' in caixa_clean:
            montepio_rows.append(row)

    print(f"✅ Encontradas {len(montepio_rows)} movimentações para MONTEPIO")

    # Agrupa por tipo e processo
    por_tipo = defaultdict(float)
    por_processo = defaultdict(lambda: {"entrada": 0, "saida": 0, "transferencia": 0, "outro": 0})

    for row in montepio_rows:
        valor = parse_valor(row.importancia)
        tipo = row.tipo.value if hasattr(row.tipo, 'value') else str(row.tipo)
        processo = row.processo or "Sem Processo"

        # Categoriza por tipo de movimento
        tipo_upper = str(tipo).upper()
        if "ENTRADA" in tipo_upper:
            por_tipo["Entradas"] += valor
            por_processo[processo]["entrada"] += valor
        elif "SAIDA" in tipo_upper:
            por_tipo["Saídas"] -= valor
            por_processo[processo]["saida"] -= valor
        elif "TRANSFER" in tipo_upper:
            por_tipo["Transferências"] -= valor if "SAIDA" in row.descricao.upper() else valor
            por_processo[processo]["transferencia"] += valor
        elif "MVP" in tipo_upper or "MVV" in tipo_upper:
            por_tipo["MVV/Repasses"] -= valor
            por_processo[processo]["outro"] -= valor
        else:
            por_tipo["Outros"] += valor
            por_processo[processo]["outro"] += valor

    # Cálculo final
    saldo_calculado = sum(por_tipo.values())

    # Relatório
    print("\n" + "=" * 110)
    print("1️⃣ RESUMO POR TIPO DE MOVIMENTO")
    print("=" * 110)

    for tipo, valor in sorted(por_tipo.items(), key=lambda x: abs(x[1]), reverse=True):
        percentual = (valor / saldo_calculado * 100) if saldo_calculado != 0 else 0
        simbolo = "📈" if valor > 0 else "📉"
        print(f"{simbolo} {tipo:<25} {valor:>15,.2f} €  ({percentual:>6.2f}%)")

    print(f"\n{'─' * 110}")
    print(f"{'SALDO CALCULADO (soma dos tipos):':<42} {saldo_calculado:>15,.2f} €")

    # Lê saldo da sheet
    try:
        caixas_ws = sheets._sh.worksheet(settings.sheet_caixas)
        caixas_headers = [str(h).strip() for h in caixas_ws.row_values(1)]
        caixas_row = caixas_ws.row_values(2)

        for i, h in enumerate(caixas_headers):
            if 'MONTEPIO' in h.upper():
                saldo_sheet = caixas_row[i] if i < len(caixas_row) else "?"
                saldo_sheet_val = parse_valor(saldo_sheet)

                print(f"{'SALDO NA SHEET:':<42} {saldo_sheet_val:>15,.2f} €")

                diferenca = saldo_calculado - saldo_sheet_val
                print(f"{'DIFERENÇA:':<42} {diferenca:>15,.2f} €")
                print("=" * 110)

                if abs(diferenca) > 0.01:
                    print(f"\n⚠️  Há uma diferença de {abs(diferenca):.2f} € entre o calculado e a sheet")
                    print("   Possíveis causas:")
                    print("   • Lançamentos ainda não reconciliados")
                    print("   • Ajustes manuais no SOMA")
                    print("   • Transferências não completamente capturadas")
                break
    except Exception as e:
        print(f"⚠️  Erro ao ler sheet de caixas: {e}")

    # Detalhamento por processo
    print("\n" + "=" * 110)
    print("2️⃣ DETALHAMENTO POR PROCESSO")
    print("=" * 110)

    print(f"\n{'Processo':<35} {'Entradas':>15} {'Saídas':>15} {'Saldo':>15}")
    print("-" * 110)

    for processo in sorted(por_processo.keys()):
        dados = por_processo[processo]
        total = sum(dados.values())
        entrada = dados["entrada"]
        saida = dados["saida"]

        if abs(total) > 0.01:  # Mostra apenas se houver saldo
            print(f"{processo:<35} {entrada:>15,.2f} {saida:>15,.2f} {total:>15,.2f}")

    # Resumo dos principais componentes
    print("\n" + "=" * 110)
    print("3️⃣ COMPONENTES PRINCIPAIS DO SALDO")
    print("=" * 110)

    # Ordena processos por valor absoluto
    processos_sorted = sorted(
        [(p, sum(d.values())) for p, d in por_processo.items()],
        key=lambda x: abs(x[1]),
        reverse=True
    )

    print("\nTop 10 processos que mais afetam o saldo:\n")
    for i, (processo, valor) in enumerate(processos_sorted[:10], 1):
        simbolo = "➕" if valor > 0 else "➖"
        print(f"{i:2d}. {simbolo} {processo:<30} {valor:>12,.2f} €")

    # Estatísticas finais
    print("\n" + "=" * 110)
    print("📊 ESTATÍSTICAS")
    print("=" * 110)
    print(f"""
Total de movimentações: {len(montepio_rows)}
Processos envolvidos:   {len(por_processo)}
Total de entradas:      {por_tipo.get('Entradas', 0):,.2f} €
Total de saídas:        {por_tipo.get('Saídas', 0):,.2f} €
Saldo final:            {saldo_calculado:,.2f} €

""")

    print("=" * 110)
    print("💡 INTERPRETAR O VALOR DO CAIXA")
    print("=" * 110)
    print("""
O valor do caixa é determinado por:

ENTRADAS (+):
  • Depósitos bancários (T_EXTRATO, Wise, PIX, etc.)
  • Doações/Dízimos/Ofertas (quando registradas como receita)
  • Vendas de Lanchonete/Café/Livraria

SAÍDAS (-):
  • Repasses (Missões 1%, COVV 2%, Dízimos 10%, etc.)
  • Transferências para outras contas
  • Cashier/Caixa Diário
  • Emolumentos bancários

Se houver diferença entre "Saldo Calculado" e "Saldo na Sheet",
pode haver lançamentos fora de CONTAORDEM ou ajustes no SOMA.

Para mais detalhes, acesse:
  https://verbodavida.info/IVV/index.php?mod=ivv&exec=caixas
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
