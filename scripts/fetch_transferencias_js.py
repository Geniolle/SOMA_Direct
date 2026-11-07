#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Descarrega o ficheiro JavaScript que controla as transferências.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Setup path
base_dir = Path(__file__).resolve().parents[1]
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession


def main():
    """Descarrega transferencias_caixas.js"""

    print("\n" + "=" * 160)
    print("📥 DESCARREGANDO: transferencias_caixas.js")
    print("=" * 160 + "\n")

    settings = Settings.from_env()
    http_session = ResilientSession(verify_tls=settings.verify_tls)

    # Autenticar
    print("🔐 Autenticando...\n")
    auth = SomaAuthenticator(settings, http_session)

    if not auth.login():
        print("❌ Falha na autenticação")
        return

    print("✅ Autenticado\n")

    # Descarregar o ficheiro JavaScript
    print("📥 Descarregando transferencias_caixas.js...\n")

    base_url = settings.site_base_url.rstrip('/')
    js_url = f"{base_url}/themes/js/transferencias_caixas.js"

    try:
        response = http_session.session.get(js_url, timeout=settings.timeout_seconds)

        if response.status_code == 200:
            print(f"✅ Ficheiro descarregado ({len(response.text)} bytes)\n")

            # Salvar o ficheiro
            output_path = Path(__file__).parent / "transferencias_caixas.js"
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(response.text)

            print(f"💾 Ficheiro salvo em: {output_path}\n")

            # Mostrar trecho com 'delete' ou 'trash'
            import re
            lines = response.text.split('\n')

            print("🔍 Procurando por 'delete', 'trash', ou 'excluir':\n")
            found = False
            for idx, line in enumerate(lines, 1):
                if any(word in line.lower() for word in ['delete', 'trash', 'excluir']):
                    print(f"Linha {idx}: {line[:150]}")
                    found = True

            if not found:
                print("⚠️  Nenhuma menção a 'delete/trash/excluir' encontrada.")
                print("\nPrimeiras 50 linhas do ficheiro:\n")
                for i, line in enumerate(lines[:50], 1):
                    print(f"{i:3d}: {line}")

        else:
            print(f"❌ Erro: HTTP {response.status_code}")
            print(f"URL: {js_url}")

    except Exception as e:
        print(f"❌ Erro ao descarregar: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 160 + "\n")


if __name__ == "__main__":
    main()
