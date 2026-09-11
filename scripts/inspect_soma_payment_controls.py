from __future__ import annotations

import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession


def main() -> None:
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    if not SomaAuthenticator(settings, http).login():
        raise RuntimeError("Falha no login do SOMA")
    for document_code in sys.argv[1:]:
        response = http.get(
            f"{settings.site_base_url.rstrip('/')}/?mod=ivv&exec=entradas_saidas_dados&ID={document_code}"
        )
        print(f"\nDOC {document_code}")
        for source in re.findall(r'<script[^>]+src=["\']([^"\']+)', response.text, re.IGNORECASE):
            if "entrada" in source.lower() or "saida" in source.lower() or "pag" in source.lower() or "baixa" in source.lower():
                print(f"SCRIPT={source}")
                script_response = http.get(f"{settings.site_base_url.rstrip('/')}/{source.lstrip('/')}")
                for marker in ("exc_baixa", "cancelar_pagamento"):
                    position = script_response.text.find(marker)
                    if position >= 0:
                        print(script_response.text[max(0, position - 800):position + 1800])
        for match in re.findall(r"<[^>]+(?:pagamento|baixa|exclu|delete)[^>]*>", response.text, re.IGNORECASE):
            print(re.sub(r"\s+", " ", match)[:500])
        for script in re.findall(r"<script[^>]*>(.*?)</script>", response.text, re.IGNORECASE | re.DOTALL):
            if "exc_baixa" in script or "cancelar_pagamento" in script:
                print(re.sub(r"\s+", " ", script)[:4000])


if __name__ == "__main__":
    main()
