from __future__ import annotations

import sys
from pathlib import Path
from gspread.utils import ValueInputOption

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService
from domain.models import normalize_document_value


def main() -> int:
    settings = Settings.from_env()
    sheets = GoogleSheetsService(settings)
    ws_co = sheets._ws
    ws_soma = sheets._sh.worksheet(settings.sheet_soma)

    print("=" * 80)
    print("APLICAÇÃO DE CORREÇÃO NAS LINHAS DE TESTE (LINHA 183 e LINHA 2155)")
    print("=" * 80)

    # 1. LINHA 183 (VC_VENDAS): Lançamento não encontrado no SOMA em 20/09/2026 com 1,00 €
    # - Validar sequencial sem colisão: 'VENDA DA CANTINA (VERBO CAFÉ) N006' (correto)
    # - Limpar DOC. SOMA (coluna E / linha 183)
    # - Coluna SOMA (coluna Y / linha 183) -> 'esperando atualizar'
    print("\n[LINHA 183]")
    print("  ID_INTERNO: VCE0000000634 | DATA: 20/09/2026 | VALOR: 1,00 €")
    print("  DESCRIÇÃO SOMA: Mantido 'VENDA DA CANTINA (VERBO CAFÉ) N006' (sequencial único correto)")
    print("  DOC. SOMA: 5467748 -> LIMPO ('')")
    print("  Coluna SOMA: 'esperando atualizar'")

    updates_co = [
        {"range": "E183", "values": [[""]]},
        {"range": "Y183", "values": [["esperando atualizar"]]},
    ]

    # 2. LINHA 2155 (DÍZIMOS/OFERTAS): DOC 4580413 confirmado e único no SOMA em 31/08/2025 com 133,80 €
    # - Alinhar DESCRIÇÃO SOMA (coluna J / linha 2155) para 'DÍZIMOS E OFERTAS (CULTO) N001'
    # - Coluna SOMA (coluna Y / linha 2155) -> 'Validado'
    print("\n[LINHA 2155]")
    print("  ID_INTERNO: ENT0000000174 | DATA: 31/08/2025 | VALOR: 133,80 € | DOC: 4580413")
    print("  DESCRIÇÃO SOMA: 'R250831 - DÍZIMOS E OFERTAS (CULTO)' -> 'DÍZIMOS E OFERTAS (CULTO) N001'")
    print("  Coluna SOMA: 'Validado'")

    updates_co.extend([
        {"range": "J2155", "values": [["DÍZIMOS E OFERTAS (CULTO) N001"]]},
        {"range": "Y2155", "values": [["Validado"]]},
    ])

    print("\n-> Gravando alterações na folha CONTAORDEM...")
    ws_co.batch_update(updates_co, value_input_option=ValueInputOption.user_entered)
    print("-> CONTAORDEM atualizada com sucesso.")

    # Alinhar descrição na Sheet SOMA para o DOC 4580413
    print("\n-> Alinhando Sheet SOMA para DOC 4580413...")
    soma_values = ws_soma.get_all_values()
    target_row = None
    for r_num, row in enumerate(soma_values[1:], start=2):
        if normalize_document_value(row[1]) == "4580413":
            target_row = r_num
            break

    if target_row:
        ws_soma.update(f"D{target_row}", [["DÍZIMOS E OFERTAS (CULTO) N001"]], value_input_option=ValueInputOption.user_entered)
        print(f"-> Sheet SOMA linha {target_row} (DOC 4580413) atualizada para 'DÍZIMOS E OFERTAS (CULTO) N001'.")

    # 3. VALIDAÇÃO PÓS-CORREÇÃO
    print("\n" + "=" * 80)
    print("VALIDAÇÃO DOS DADOS APÓS A GRAVAÇÃO")
    print("=" * 80)

    co_fresh = ws_co.get_all_values()
    headers = co_fresh[0]

    for r in (183, 2155):
        row = co_fresh[r - 1]
        print(f"\nLinha {r} na CONTAORDEM:")
        print(f"  DATA MOV.:       {row[0]}")
        print(f"  IMPORTÂNCIA:     {row[3]}")
        print(f"  DOC. SOMA:       {repr(row[4])}")
        print(f"  DESCRIÇÃO SOMA:  {repr(row[9])}")
        print(f"  ORIGEM:          {repr(row[23])}")
        print(f"  SOMA (Coluna Y): {repr(row[24])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
