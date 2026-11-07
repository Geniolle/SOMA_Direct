from config.settings import Settings
from services.sheets_service import GoogleSheetsService
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
import re
from decimal import Decimal

settings = Settings.from_env()
sheets = GoogleSheetsService(settings)
all_rows = sheets.get_all_rows(only_entrada_saida=False)
mvv_rows = [r for r in all_rows if str(r.raw.get('TIPO', '')).strip().upper() == 'MVV']

# Parse SOMA reports for 2024, 2025, 2026
MONTHS_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
}

# We already fetched t=1 and t=2 for 2024, 2025, 2026 in scratch files
# Let's inspect each year's data
def parse_decimal(s):
    return Decimal(s.replace('.', '').replace(',', '.'))

# Let's collect all SOMA repasses by month and status
# 2024:
# Jan: 643,74 -> Não Conferido
# Fev: 664,48 -> Conferido
# Mar: 701,45 -> Conferido
# Abr: 644,76 -> Conferido

# 2025:
# Dez: 612,35 -> Não Conferido

# 2026:
# Jan: 681,47 -> Conferido
# Fev: 921,57 -> Conferido
# Mar: 517,03 -> Não Conferido
# Ago: 776,25 -> Não Conferido

# Wait, what about other months in 2024, 2025, 2026?
# Let's check the remaining 15 rows:
print("CONTAORDEM MVV ROWS:")
for r in sorted(mvv_rows, key=lambda x: x.row_number, reverse=True):
    print(f"Row {r.row_number:4d} | {r.data_mov} | {r.importancia:>10} | {r.id_interno}")
