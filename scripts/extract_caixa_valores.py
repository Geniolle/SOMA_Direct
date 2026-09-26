#!/usr/bin/env python
"""
Extração de valores de Caixas via o endpoint oficial do SOMA.
Usa o mesmo método que o dashboard do SOMA utiliza (via SomaApiService).
"""

from __future__ import annotations

import sys
import re
from html import unescape
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 110)
    print("EXTRAÇÃO DE VALORES DE CAIXAS - ENDPOINT OFICIAL DO SOMA")
    print("=" * 110)

    # Autenticação
    print("\n[1/3] Autenticando no SOMA...")
    if not auth.login():
        print("❌ Falha na autenticação")
        return 1
    print("✅ Autenticado com sucesso")

    # Acesso ao endpoint oficial
    print("\n[2/3] Acessando endpoint oficial: sys/post/buscarResumoCaixasNew.php")

    base_url = settings.site_base_url.rstrip("/")
    endpoint_url = f"{base_url}/sys/post/buscarResumoCaixasNew.php"

    try:
        response = http.post_ajax(
            endpoint_url,
            data={"id": settings.institution_id},
            timeout=10
        )

        if response.status_code != 200:
            print(f"❌ Erro: Status {response.status_code}")
            return 1

        print(f"✅ Endpoint acessado (Status: {response.status_code})")
        print(f"   Tamanho da resposta: {len(response.text)} bytes")

    except Exception as e:
        print(f"❌ Erro de conexão: {e}")
        return 1

    # Parsing da resposta
    print("\n[3/3] Extraindo valores de caixas...")

    # Padrão: class="counter-number-related">VALOR</div>...</div><div class="counter-label">NOME</div>
    # Vem do método soma_api_service.py line 140-148

    caixas = {}
    for match in re.finditer(
        r'counter-number-related[^>]*>([^<]*)<.*?counter-label[^>]*>\s*([^<]+?)\s*</div>',
        response.text,
        re.DOTALL
    ):
        valor = unescape(match.group(1)).strip()
        label = unescape(re.sub(r'\s+', ' ', match.group(2))).strip()

        if label and valor:
            caixas[label] = valor
            print(f"   ✅ Encontrado: {label:<50} = {valor}")

    # Resultados
    print("\n" + "=" * 110)
    print(f"RESULTADO: {len(caixas)} CAIXA(S) EXTRAÍDA(S)")
    print("=" * 110)

    if caixas:
        print("\n📊 VALORES DOS CAIXAS:\n")

        for i, (nome, valor) in enumerate(caixas.items(), 1):
            print(f"{i:2d}. {nome:<50} {valor:>15}")

        # Procura pela MONTEPIO especificamente
        print("\n" + "=" * 110)
        print("🎯 CAIXA ECONÔMICA MONTEPIO")
        print("=" * 110)

        montepio_found = False
        for nome, valor in caixas.items():
            if 'MONTEPIO' in nome.upper():
                print(f"\n✅ ENCONTRADO: {nome}")
                print(f"   Valor: {valor} €")
                montepio_found = True

                # Análise
                print(f"\n📋 COMPOSIÇÃO DESTE VALOR:")
                print(f"""
   Este valor é calculado pelo SOMA com a seguinte lógica:

   1. ENTRADAS (adicionadas):
      • Depósitos bancários (Wise, PIX, etc.)
      • Doações, Dízimos, Ofertas
      • Vendas Lanchonete, Café, Livraria

   2. SAÍDAS (subtraídas):
      • Repasses Institucionais (10%, 2%, 1%, 2%)
      • Transferências para Caixa Diário
      • Emolumentos bancários
      • Outras deduções

   3. FÓRMULA:
      Saldo = Total Entradas - Total Saídas

   4. ATUALIZAÇÃO:
      O SOMA recalcula este valor cada vez que:
      • Uma entrada é registrada
      • Uma saída/repasse é realizado
      • Uma transferência é feita
      • Um emolumento é cobrado
""")
                break

        if not montepio_found:
            print("\n⚠️  Caixa MONTEPIO não encontrado na resposta")
            print("\nCaixas retornadas pelo SOMA:")
            for nome in sorted(caixas.keys()):
                print(f"  • {nome}")

        return 0
    else:
        print("\n❌ Nenhum caixa foi extraído")
        print("\nPrimeiros 1000 caracteres da resposta:")
        print(f"\n{response.text[:1000]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
