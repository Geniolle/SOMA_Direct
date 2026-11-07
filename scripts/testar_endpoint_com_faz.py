#!/usr/bin/env python3
"""
Testa endpoint de Transferência com parâmetro 'faz'.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import re
import time
from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.soma_api_service import SomaApiService, clean_amount

def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    auth = SomaAuthenticator(settings, http)
    api = SomaApiService(settings, http)

    print("=" * 120)
    print("TESTE: Endpoint com parâmetro 'faz'")
    print("=" * 120)

    # Autenticar
    print("\n✓ Autenticando...")
    if not auth.login():
        print("❌ Erro de autenticação")
        return

    api.load_catalogs()

    # Dados de teste
    cx_origem = api.resolve_caixa_id("CAIXA DIÁRIO")
    cx_destino = api.resolve_caixa_id("CAIXA ECONÔMICA MONTEPIO GERAL [CONTA CORRENTE]")

    payload = {
        "id_transferencia_caixa": "",
        "aceitar_caixa_negativo": "1",
        "add": "1",
        "id_inst": settings.institution_id,
        "id_caixa_origem": cx_origem,
        "valor_transferencia": clean_amount("25.00"),
        "id_caixa_destino": cx_destino,
        "valor_transferencia_entrada": clean_amount("25.00"),
        "data_transferencia": "26/09/2026",
        "obs": "TESTE - Com parâmetro faz"
    }

    base_url = api.base_url.rstrip("/")

    endpoints_to_test = [
        "mod=app&exec=transferencias_caixas&faz=dados",
        "mod=app&exec=transferencias_caixas&faz=adicionar",
        "mod=app&exec=transferencias_caixas&faz=salvar",
        "mod=ivv&exec=transferencias_caixas&faz=dados",
        "mod=app&exec=adicionar_transferencias_caixas&faz=dados",
    ]

    print("\n📋 Testando endpoints com 'faz':\n")

    for endpoint in endpoints_to_test:
        url = f"{base_url}?{endpoint}"
        print(f"  🔬 {endpoint}")

        try:
            resp = http.post(url, data=payload)
            print(f"     Status: {resp.status_code}")

            # Verificar resposta
            if resp.status_code != 200:
                print(f"     ✗ HTTP error")
                continue

            # Procurar por sucesso
            success_keywords = ["sucesso", "criada", "transferencia criada", "transferência criada"]
            has_success = any(kw in resp.text.lower() for kw in success_keywords)

            if has_success:
                print(f"     ✓ SUCESSO detectado na response!")
                print(f"     URL FINAL: {url}")

                # Verificar no SOMA
                time.sleep(1)
                transfer_id = api._find_transfer_id(valor="25.00", data_mov="26/09/2026")
                if transfer_id:
                    print(f"     ✓✓ CONFIRMADO: Transferência ID {transfer_id}")
                    return
                else:
                    print(f"     ⚠️  Sucesso na response, mas não encontrada no SOMA")

            # Procurar por erro
            if "erro" in resp.text.lower():
                erro_m = re.search(r'["\'](?:erro|message)["\']:\s*["\']([^"\']+)["\']', resp.text)
                if erro_m:
                    print(f"     ✗ Erro: {erro_m.group(1)[:80]}")
                else:
                    print(f"     ✗ Erro (sem detalhe)")
            else:
                print(f"     → Resposta OK, mas sem indicadores óbvios")

        except Exception as e:
            print(f"     ❌ Exceção: {str(e)[:80]}")

if __name__ == "__main__":
    main()
