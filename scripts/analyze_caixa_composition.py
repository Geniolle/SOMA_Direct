#!/usr/bin/env python
"""
Analisa a composição do caixa CAIXA ECONÔMICA MONTEPIO GERAL - CC
através dos dados da sheet GERENCIAR CAIXAS e das movimentações em CONTAORDEM.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from domain.models import norm_basic, clean_amount_for_comparison


def main():
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)

    print("=" * 100)
    print("ANÁLISE DE COMPOSIÇÃO DO CAIXA - CAIXA ECONÔMICA MONTEPIO GERAL - CC")
    print("=" * 100)

    # 1. Ler saldos de caixas
    print("\n[1/3] Lendo saldos da sheet 'GERENCIAR CAIXAS'...")
    try:
        caixas_ws = sheets._sh.worksheet(settings.sheet_caixas)
        caixas_headers = [str(h).strip() for h in caixas_ws.row_values(1)]
        caixas_row = caixas_ws.row_values(2)

        caixa_map = {norm_basic(h): i for i, h in enumerate(caixas_headers)}

        # Procura pela coluna CAIXA ECONÔMICA MONTEPIO
        target_col_idx = None
        target_header = None

        # Procura exato
        for original_header in caixas_headers:
            if 'MONTEPIO' in original_header.upper():
                target_col_idx = caixas_headers.index(original_header)
                target_header = original_header
                valor_caixa = caixas_row[target_col_idx] if target_col_idx < len(caixas_row) else "?"
                print(f"✅ Encontrado: '{original_header}'")
                print(f"   Valor Atual (Sheet): {valor_caixa} €")
                break

        if not target_col_idx:
            print("❌ Caixa MONTEPIO não encontrado na sheet")
            print(f"\n   Colunas disponíveis:")
            for h in caixas_headers[:15]:
                print(f"     • {h}")
            return 1
    except Exception as e:
        print(f"❌ Erro ao acessar sheet de caixas: {e}")
        return 1

    # 2. Analisar movimentações em CONTAORDEM
    print("\n[2/3] Analisando movimentações em CONTAORDEM...")
    try:
        # Inclui TODAS as movimentações (não apenas entrada/saída)
        all_rows = sheets.get_all_rows(only_entrada_saida=False)
        print(f"✅ Lidas {len(all_rows)} linhas de CONTAORDEM (todas as movimentações)")

        # Agrupa por tipo de movimento e caixa
        por_caixa = defaultdict(lambda: {"entradas": 0, "saidas": 0, "transferencias": 0, "repasses": 0, "outros": 0, "count": 0})
        movimentacoes_montepio = []

        for row in all_rows:
            # Verifica se é para Montepio
            if 'MONTEPIO' not in clean_caixa(row.caixa).upper():
                continue

            try:
                valor = float(row.importancia.replace(',', '.').replace('€', '').strip() or 0)
            except:
                valor = 0

            mov_type = row.tipo.value if hasattr(row.tipo, 'value') else str(row.tipo)

            if 'ENTRADA' in str(mov_type).upper():
                por_caixa['MONTEPIO']["entradas"] += valor
            elif 'SAIDA' in str(mov_type).upper():
                por_caixa['MONTEPIO']["saidas"] += valor

            por_caixa['MONTEPIO']["count"] += 1
            movimentacoes_montepio.append({
                "data": row.data_mov,
                "descricao": row.descricao[:50],
                "tipo": mov_type,
                "valor": valor,
                "processo": row.processo
            })

        if movimentacoes_montepio:
            print(f"✅ Encontradas {len(movimentacoes_montepio)} movimentações para MONTEPIO")

            stats = por_caixa['MONTEPIO']
            print(f"\n   📊 Resumo de Movimentações:")
            print(f"     • Total de Entradas: {stats['entradas']:.2f} €")
            print(f"     • Total de Saídas: {stats['saidas']:.2f} €")
            print(f"     • Saldo (Entrada - Saída): {stats['entradas'] - stats['saidas']:.2f} €")
            print(f"     • Número de registros: {stats['count']}")
    except Exception as e:
        print(f"⚠️  Erro ao analisar CONTAORDEM: {e}")
        movimentacoes_montepio = []

    # 3. Análise por componentes (processo/descrição)
    print("\n[3/3] Análise por Processo/Descrição...")
    por_processo = defaultdict(float)

    for mov in movimentacoes_montepio:
        processo = mov["processo"] or "Sem processo"
        valor = mov["valor"]

        # Ajusta sinal baseado no tipo
        if "SAIDA" in str(mov["tipo"]).upper():
            valor = -valor

        por_processo[processo] += valor

    if por_processo:
        print("\n   Composição por Origem/Processo:")
        for processo, total in sorted(por_processo.items(), key=lambda x: abs(x[1]), reverse=True):
            simbolo = "📈" if total >= 0 else "📉"
            print(f"   {simbolo} {processo:<30} {total:>12.2f} €")

    # Resumo final
    print("\n" + "=" * 100)
    print("RESUMO EXECUTIVO")
    print("=" * 100)
    print(f"""
📊 CAIXA: CAIXA ECONÔMICA MONTEPIO GERAL - CC

   Saldo na Sheet:        {valor_caixa} €
   Movimentações Totais:  {len(movimentacoes_montepio)} registros

🔢 COMPOSIÇÃO DO VALOR:
   Entradas (Receitas):   {por_caixa['MONTEPIO']['entradas']:>10.2f} €
   Saídas (Repasses):     {por_caixa['MONTEPIO']['saidas']:>10.2f} €
   ─────────────────────────────
   Saldo Líquido:         {por_caixa['MONTEPIO']['entradas'] - por_caixa['MONTEPIO']['saidas']:>10.2f} €

📋 Últimas 5 movimentações:
""")

    if movimentacoes_montepio:
        for mov in sorted(movimentacoes_montepio, key=lambda x: x['data'], reverse=True)[:5]:
            tipo_icon = "➕" if "ENTRADA" in str(mov['tipo']).upper() else "➖"
            print(f"   {tipo_icon} {mov['data']:<12} | {mov['descricao']:<30} | {mov['valor']:>8.2f} € | {mov['processo']}")

    print("\n" + "=" * 100)
    print("💡 NOTAS")
    print("=" * 100)
    print("""
• Estes dados vêm da Sheet CONTAORDEM (reconciliação local)
• Para confirmar o saldo oficial no SOMA, acesse:
  https://verbodavida.info/IVV/index.php?mod=ivv&exec=caixas

• Componentes típicos do caixa:
  ✓ Entradas: Doações, Dízimos, Ofertas, Lanchonete, Livraria
  ✗ Saídas:   Repasses (MVV), Transferências, Cashier, Outras deduções

• Se houver divergências, execute:
  python scripts/validate_check_column.py --dry-run
    """)

    return 0


def clean_caixa(caixa: str) -> str:
    """Remove sufixos entre colchetes e normaliza."""
    import re
    c = re.sub(r'\[.*?\]', '', caixa).strip()
    return norm_basic(c)


if __name__ == "__main__":
    sys.exit(main())
