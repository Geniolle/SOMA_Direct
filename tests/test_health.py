import os
import sys
import pytest
from pathlib import Path
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.http_session import ResilientSession
from core.auth import SomaAuthenticator
from services.soma_api_service import SomaApiService

def test_connection():
    if os.getenv("RUN_SOMA_LIVE_TESTS") != "1":
        pytest.skip("teste de integração real; defina RUN_SOMA_LIVE_TESTS=1 para executar")
    settings = Settings.from_env()
    if not settings.site_user or not settings.site_password:
        pytest.skip("credenciais do SOMA não configuradas")
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)
    assert auth.login() is True
    api = SomaApiService(settings, http)
    api.load_catalogs()
    assert len(api._plano_contas_map) > 0
    print("Health check OK! Autenticacao e catalogos carregados.")

if __name__ == "__main__":
    test_connection()
