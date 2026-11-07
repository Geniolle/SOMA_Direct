#!/usr/bin/env python3
"""
Testa diferentes endpoints para criar Transferências.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import re
from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.soma_api_service import SomaApiService, clean_amount

def test_endpoint(http, base_url, endpoint_params, payload, nome):
    """Testa um endpoint específico."""
    url = f"{base_url}?{endpoint_params}"
    print(f"\n  🔬 Testando: {nome}")
    print(f"     URL: {url}")

    try:
        resp = http.post(url, data=payload)
        print(f"     Status: {resp.status_code}")

        # Procurar por mensagens de sucesso
        if "sucesso" in resp.text.lower() or "criada" in resp.text.lower():
            print(f"     ✓ Mensagem de SUCESSO encontrada!")
            return True

        # Procurar por erros
        if "erro" in resp.text.lower():
            erro_match = re.search(r'["\'](?:erro|message)["\']:\s*["\']([^"\']+)["\']', resp.text)
            if erro_match:
                print(f"     ✗ Erro: {erro_match.group(1)}")

        # Procurar por redireção
        if "redirect" in resp.headers:
            print(f"     → Redireção para: {resp.headers['redirect']}")

        return False
    except Exception as e:
        print(f"     ❌ Exceção: {e}")
        return False

def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    auth = SomaAuthenticator(settings, http)
    api = SomaApiService(settings, http)

    print("=" * 120)
    print("BUSCA: Endpoint Correto para Criar Transferências")
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
        "obs": "TESTE - Endpoint Discovery"
    }

    base_url = api.base_url.rstrip("/")

    print("\n📋 Testando diferentes endpoints:\n")

    endpoints = [
        ("mod=ivv&exec=transferencias_caixas_dados", "Endpoint atual (mod=ivv)"),
        ("mod=app&exec=transferencias_caixas_dados", "Endpoint alternativo (mod=app)"),
        ("mod=app&exec=adicionar_transferencias_caixas", "Adicionar Transferências"),
        ("mod=ivv&exec=transferencias_caixas", "Sem _dados"),
        ("mod=app&exec=transferencias_caixas", "App sem _dados"),
        ("mod=ivv&exec=salvar_transferencias", "Salvar Transferências"),
        ("mod=app&exec=salvar_transferencias", "App Salvar"),
    ]

    found = False
    for endpoint, nome in endpoints:
        if test_endpoint(http, base_url, endpoint, payload, nome):
            found = True
            print(f"\n✅ ENCONTRADO: {endpoint}")
            break

    if not found:
        print("\n❌ Nenhum endpoint funcionou nos testes rápidos.")
        print("\n🔍 Próximos passos:")
        print("   1. Verificar logs do SOMA para descobrir o endpoint real")
        print("   2. Usar Browser DevTools para capturar o POST real")
        print("   3. Procurar em ficheiros JavaScript do SOMA")

if __name__ == "__main__":
    main()
