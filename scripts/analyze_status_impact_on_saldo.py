#!/usr/bin/env python
"""
Investigação Crítica: O STATUS (Conferido/Não conferido) afeta o saldo do caixa?

Pergunta: O SOMA só deduz saídas que estão "Conferidas"?
          Ou deduz também as "Não conferidas"?
"""

from __future__ import annotations

import re
import sys
import logging
from pathlib import Path
from html import unescape
from collections import defaultdict

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("analyze_status_impact")


def extract_saidas_with_status(http, base_url, settings, query):
    """Busca saídas e extrai o status de cada uma."""
    search_payload = {
        "pesquisa": query,
        "filtro": "descricao",
        "id_inst": settings.institution_id,
        "tipo": "0",  # SAÍDA
        "v": "1",
        "s": "2",
        "t_d": "1",
        "cc": "-1",
        "c": "",
        "i": "01/01/2024",
        "f": "31/12/2026",
    }

    response = http.post_ajax(
        f"{base_url}/sys/post/buscarEntradasSaidas.php",
        data=search_payload,
        timeout=10
    )

    saidas = []
    rows = []

    # Parse tabela
    for row_html in re.findall(r'<tr\b[^>]*>(.*?)</tr>', response.text, re.DOTALL):
        cells = [unescape(re.sub(r'<[^>]+>', ' ', c)).strip()
                 for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row_html, re.DOTALL)]
        if len(cells) >= 7:  # Precisa de pelo menos 7 colunas
            rows.append(cells)

    # Pula cabeçalho e processa dados
    for row in rows[1:]:  # Skip header
        if len(row) >= 7:
            codigo = row[2].strip() if len(row) > 2 else ""
            descricao = row[4].strip() if len(row) > 4 else ""
            valor = row[5].strip() if len(row) > 5 else ""
            status = row[7].strip() if len(row) > 7 else "?"

            if codigo and valor:
                saidas.append({
                    "codigo": codigo,
                    "descricao": descricao,
                    "valor": valor,
                    "status": status,
                    "query": query
                })

    return saidas


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 130)
    print("INVESTIGAÇÃO CRÍTICA: O STATUS (Conferido/Não conferido) AFETA O SALDO DO CAIXA?")
    print("=" * 130)

    # Autenticação
    print("\n[1/5] Autenticando...")
    if not auth.login():
        print("❌ Falha na autenticação")
        return 1
    print("✅ Autenticado")

    base_url = settings.site_base_url.rstrip("/")

    # Buscar todas as saídas de repasse
    print("\n[2/5] Buscando TODAS as saídas de repasse...")

    all_saidas = []
    queries = ["DÍZIMO", "COVV", "MISSÕES", "NOVAS OBRAS", "LIVRARIA"]

    for query in queries:
        try:
            saidas = extract_saidas_with_status(http, base_url, settings, query)
            all_saidas.extend(saidas)
            print(f"      ✅ {query}: {len(saidas)} saídas encontradas")
        except Exception as e:
            print(f"      ⚠️  {query}: Erro - {e}")

    # Analisar status
    print("\n[3/5] Analisando distribuição de STATUS...")

    por_status = defaultdict(list)
    for saida in all_saidas:
        status = saida["status"]
        por_status[status].append(saida)

    print(f"\n   Total de saídas encontradas: {len(all_saidas)}")
    print(f"\n   Distribuição por STATUS:")

    total_conferido = 0
    total_nao_conferido = 0

    for status, saidas in sorted(por_status.items()):
        count = len(saidas)
        # Tenta somar valores
        total_valor = 0
        for s in saidas:
            try:
                val_str = s["valor"].replace("€", "").replace(",", ".").strip()
                total_valor += float(val_str)
            except:
                pass

        print(f"\n   📊 STATUS: '{status}'")
        print(f"      Quantidade: {count} saídas")
        print(f"      Valor Total: {total_valor:.2f} €")

        if "conferido" in status.lower() and "não" not in status.lower():
            total_conferido += total_valor
        elif "não" in status.lower() or "não conferido" in status.lower():
            total_nao_conferido += total_valor

        # Mostra exemplos
        print(f"      Exemplos:")
        for saida in saidas[:3]:
            print(f"         • {saida['codigo']}: {saida['descricao'][:50]:<50} = {saida['valor']}")

    # Cálculo teórico do saldo
    print("\n[4/5] Análise teórica do cálculo de saldo...")

    print(f"""
    🤔 QUESTÃO CRÍTICA: Como o SOMA calcula o saldo?

    HIPÓTESE 1: Deduz APENAS saídas "Conferidas"
    ├─ Saldo = Entradas - (Saídas Conferidas apenas)
    └─ Saídas "Não conferidas" NÃO afetam o saldo

    HIPÓTESE 2: Deduz TODAS as saídas (Conferidas + Não conferidas)
    ├─ Saldo = Entradas - (Todas as Saídas)
    └─ Saídas "Não conferidas" TAMBÉM afetam o saldo

    HIPÓTESE 3: Deduz por tipo de conferência
    ├─ Deduz saídas "Conferidas"
    ├─ Coloca "Não conferidas" em pendência
    └─ Status afeta visualmente mas não calcula

    ---

    EVIDÊNCIA DOS DADOS:

    Saídas Conferidas encontradas: {total_conferido:.2f} €
    Saídas Não Conferidas encontradas: {total_nao_conferido:.2f} €

    Saldo Atual do Caixa: -147,44 €

    Se apenas Conferidas fossem deduzidas:
      Saldo seria maior (menos deduções)

    Se todas fossem deduzidas:
      Saldo seria exatamente a diferença entre entradas e todas saídas
    """)

    # Query SQL para verificar
    print("\n[5/5] Query SQL para determinar o cálculo...")

    print("""
    🔍 PARA DETERMINAR QUAL HIPÓTESE É CORRETA:

    Query A (se apenas Conferidas):
    ─────────────────────────────────
    SELECT
        SUM(CASE WHEN tipo = 1 THEN valor ELSE 0 END) as entradas,
        SUM(CASE WHEN tipo = 0 AND status = 'Conferido' THEN valor ELSE 0 END) as saidas_conferidas,
        SUM(CASE WHEN tipo = 0 AND status != 'Conferido' THEN valor ELSE 0 END) as saidas_pendentes,
        SUM(CASE WHEN tipo = 1 THEN valor ELSE 0 END) -
        SUM(CASE WHEN tipo = 0 AND status = 'Conferido' THEN valor ELSE 0 END) as saldo
    FROM transacao
    WHERE id_caixa = (SELECT id FROM caixa WHERE nome LIKE '%MONTEPIO%');

    Query B (se todas são deduzidas):
    ────────────────────────────────
    SELECT
        SUM(CASE WHEN tipo = 1 THEN valor ELSE 0 END) as entradas,
        SUM(CASE WHEN tipo = 0 THEN valor ELSE 0 END) as saidas_total,
        SUM(CASE WHEN tipo = 1 THEN valor ELSE 0 END) -
        SUM(CASE WHEN tipo = 0 THEN valor ELSE 0 END) as saldo
    FROM transacao
    WHERE id_caixa = (SELECT id FROM caixa WHERE nome LIKE '%MONTEPIO%');

    ---

    ✅ CONFIRMAÇÃO NECESSÁRIA:

    Para confirmar qual é a lógica correta, precisava:
    1. Acessar o código-fonte de buscarResumoCaixasNew.php (no servidor SOMA)
    2. Ou consultar a view/procedure que calcula saldo
    3. Ou comparar: saldo esperado vs saldo real com diferentes status
    """)

    # Recomendação
    print("\n" + "=" * 130)
    print("RECOMENDAÇÃO DE VALIDAÇÃO")
    print("=" * 130)

    print("""
    Para determinar se o STATUS é considerado, você deveria:

    ✅ MÉTODO 1: Análise Comparativa
    ├─ Calcular: Total Entradas - Total Saídas Conferidas = X
    ├─ Calcular: Total Entradas - Total Saídas (todas) = Y
    ├─ Comparar com: Saldo Real Mostrado (-147,44 €)
    └─ Se saldo == X → Apenas Conferidas são deduzidas
       Se saldo == Y → Todas as saídas são deduzidas

    ✅ MÉTODO 2: Teste Prático
    ├─ Criar uma saída "Não conferida" manualmente
    ├─ Observar se o saldo do caixa muda imediatamente
    ├─ Se muda → Não conferidas também são deduzidas
    └─ Se não muda → Apenas conferidas são deduzidas

    ✅ MÉTODO 3: Inspecionar Código-Fonte (requer acesso ao servidor SOMA)
    ├─ Ler: /sys/post/buscarResumoCaixasNew.php
    ├─ Procurar por: WHERE status = 'Conferido' (ou similar)
    ├─ Se encontrar → Apenas conferidas
    └─ Se não encontrar → Todas são deduzidas
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
