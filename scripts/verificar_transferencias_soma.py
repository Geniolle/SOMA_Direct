#!/usr/bin/env python3
"""
Verifica se as Transferências existem no SOMA.
"""
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from services.soma_api_service import SomaApiService
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession

def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    auth = SomaAuthenticator(settings, http)
    api = SomaApiService(settings, http)
    sheets = GoogleSheetsService(settings)

    print("=" * 100)
    print("VERIFICAÇÃO: Existência de Transferências no SOMA")
    print("=" * 100)

    # Autenticar
    if not auth.login():
        print("❌ Erro de autenticação no SOMA")
        return

    api.load_catalogs()

    # Transferências a verificar
    linhas_transferencias = [2454, 3736]

    print("\n🔐 Autenticado no SOMA\n")

    for linha_num in linhas_transferencias:
        row = sheets.get_row(linha_num)
        if not row:
            print(f"❌ Linha {linha_num} não encontrada!")
            continue

        print(f"📍 LINHA {linha_num}")
        print("-" * 100)
        print(f"  Data: {row.data_mov}")
        print(f"  Valor: {row.importancia} EUR")
        print(f"  Caixa Origem: {row.caixa_saida}")
        print(f"  Caixa Destino: {row.caixa}")

        # Procurar no SOMA
        print(f"\n  Procurando no SOMA...")
        transfer_id = api._find_transfer_id(valor=row.importancia, data_mov=row.data_mov)

        if transfer_id:
            print(f"  ✓ ENCONTRADA: ID {transfer_id}")
            print(f"    → Documento: TRF_{transfer_id}")
        else:
            print(f"  ✗ NÃO ENCONTRADA no SOMA")
            print(f"    → Motivos possíveis:")
            print(f"      1. Transferência nunca foi criada")
            print(f"      2. Valor ou data não correspondem exatamente")
            print(f"      3. Transferência foi apagada do SOMA")

        print()

if __name__ == "__main__":
    main()
