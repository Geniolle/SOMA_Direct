"""
Script de execução para resolução automatizada das divergências de auditoria.
Aplica as 4 regras de conciliação no SOMA, Google Sheets CONTAORDEM e planilhas de origem.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.audit_service import AuditService
from services.reconciliation_resolver import ReconciliationResolver
from services.sheets_service import GoogleSheetsService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("soma_direct.resolve_all")


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolução automatizada das divergências")
    parser.add_argument("--dry-run", action="store_true", help="Apenas simula as resoluções sem gravar")
    args = parser.parse_args()

    print("=" * 75)
    print(">>> RESOLUÇÃO AUTOMATIZADA DE DIVERGÊNCIAS (REGRAS 1 A 4) <<<")
    print(f"Modo: {'SIMULAÇÃO (DRY RUN)' if args.dry_run else 'APLICAÇÃO REAL'}")
    print("=" * 75)

    settings = Settings.from_env()
    session = ResilientSession()
    auth = SomaAuthenticator(settings, session)
    auth.login()

    sheets = GoogleSheetsService(settings)
    audit = AuditService(settings, session, sheets)
    resolver = ReconciliationResolver(settings, session, sheets, audit)

    sheet_updates, origin_updates, soma_updates = resolver.build_resolutions()

    print(f"Total de atualizações calculadas:")
    print(f"  • Planilha CONTAORDEM: {len(sheet_updates)} linhas")
    print(f"  • Planilha Origem (T_EXTRATO): {len(origin_updates)} células")
    print(f"  • Portal SOMA: {len(soma_updates)} documentos\n")

    print("DETALHE DAS AÇÕES:")
    for su in sheet_updates:
        print(f"  -> CONTAORDEM Linha {su['row_idx']:4d}: Aud={su.get('auditoria')} | NewDoc={su.get('new_doc')} | NewTipo={su.get('new_tipo')} | NewDesc={su.get('new_desc')}")
    for ou in origin_updates:
        print(f"  -> ORIGEM {ou['sheet']} Linha {ou['row']:4d} Col {ou['col']}: '{ou['val']}'")
    for so in soma_updates:
        print(f"  -> SOMA DOC {so['doc']}: '{so['new_desc']}'")

    if args.dry_run:
        print("\n[DRY RUN] Nenhuma alteração foi gravada.")
        return

    print("\nAplicando resoluções...")
    resolver.apply_resolutions(sheet_updates, origin_updates, soma_updates)
    print("\nResoluções aplicadas com sucesso!")


if __name__ == "__main__":
    main()
