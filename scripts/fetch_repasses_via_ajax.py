#!/usr/bin/env python
"""
Fetch repasses de agosto 2026 via AJAX
Os dados são carregados dinamicamente na página
"""

from __future__ import annotations

import re
import sys
import logging
from pathlib import Path
from html import unescape

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("fetch_repasses_ajax")


def main():
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    auth = SomaAuthenticator(settings, http)

    print("=" * 130)
    print("FETCH REPASSES DE AGOSTO 2026 VIA AJAX")
    print("=" * 130)

    # Autenticação
    print("\n[1/3] Autenticando...")
    if not auth.login():
        return 1
    print("✅ Autenticado")

    base_url = settings.site_base_url.rstrip("/")

    # Tentar diferentes endpoints de AJAX
    print("\n[2/3] Tentando acessar endpoints de AJAX para dados de repasses...")

    endpoints_to_try = [
        ("sys/relatorios/relatorios_repasse.php", {
            "ano": "2026",
            "mes": "08",
            "id_inst": settings.institution_id,
        }),
        ("sys/post/buscarRepasses.php", {
            "ano": "2026",
            "mes": "08",
            "id_inst": settings.institution_id,
            "v": "1",
        }),
        ("sys/post/listarRepasses.php", {
            "data_inicio": "01/08/2026",
            "data_fim": "31/08/2026",
            "id_inst": settings.institution_id,
        }),
        ("index.php", {
            "mod": "ivv",
            "exec": "relatorios",
            "rel": "repasse",
            "ano": "2026",
            "mes": "08",
            "id_inst": settings.institution_id,
        }),
    ]

    found_repasses = False

    for endpoint, params in endpoints_to_try:
        try:
            url = f"{base_url}/{endpoint}"
            print(f"\n   Tentando: {endpoint}...")

            response = http.post_ajax(url, data=params, timeout=10)

            if response.status_code == 200 and len(response.text) > 100:
                # Procura por repasses
                if any(term in response.text.upper() for term in ['DÍZIMO', 'COVV', 'MISSÕES', 'NOVAS', 'LIVRARIA']):
                    print(f"      ✅ Endpoint retornou dados!")

                    # Procura por valores
                    valores = re.findall(r'([\d.,]+)\s*€?', response.text)
                    print(f"      Valores encontrados: {len(valores)}")

                    # Salva resposta
                    save_file = Path(__file__).parent.parent / f"scratch_repasses_{endpoint.replace('/', '_')}.html"
                    save_file.write_text(response.text, encoding='utf-8')
                    print(f"      Resposta salva em: {save_file.name}")

                    found_repasses = True
                    break
        except Exception as e:
            logger.debug(f"Erro em {endpoint}: {e}")

    if not found_repasses:
        print("\n   ⚠️  Nenhum endpoint AJAX retornou dados de repasses")

    # Relatório final
    print("\n[3/3] Relatório Final...")

    print("\n" + "=" * 130)
    print("INSTRUÇÕES PARA OBTER OS VALORES MANUALMENTE")
    print("=" * 130)

    print("""
    👉 PASSO A PASSO MANUAL:

    1. Acesse: https://verbodavida.info/IVV/index.php?mod=ivv&exec=repasses

    2. Selecione o ano: 2026

    3. Selecione o mês: AGOSTO

    4. Clique em "CONSULTAR" ou "BUSCAR"

    5. A página mostrará uma tabela com os 5 repasses de agosto/2026:

       ┌─────────────────────────────────────┬────────────┐
       │ Repasse                             │ Valor      │
       ├─────────────────────────────────────┼────────────┤
       │ DÍZIMOS 10%                         │ __________ │
       │ OFERTA COVV 2%                      │ __________ │
       │ OFERTA MISSÕES 1%                   │ __________ │
       │ OFERTA NOVAS OBRAS 2%               │ __________ │
       │ REPASSE LIVRARIA 3%                 │ __________ │
       │                                     │            │
       │ TOTAL                               │ __________ │
       └─────────────────────────────────────┴────────────┘

    6. Anote os valores acima

    7. Compare com:
       - O saldo do caixa (-147,44 €)
       - Os valores que você esperava

    ❓ DIAGNÓSTICO:

    Se os valores no SOMA são DIFERENTES dos esperados:
    ├─ Pode haver erro no cálculo dos repasses
    ├─ Pode haver valores faltando ou duplicados
    ├─ Pode haver diferença entre o que foi "Conferido" vs "Não conferido"
    └─ Pode haver repasses de meses anteriores/posteriores

    Se o TOTAL dos repasses for DIFERENTE do saldo:
    ├─ Pode haver outras saídas não contabilizadas
    ├─ Pode haver transferências de caixa
    ├─ Pode haver emolumentos bancários
    └─ Pode haver entradas não contabilizadas
    """)

    return 0


if __name__ == "__main__":
    sys.exit(main())
