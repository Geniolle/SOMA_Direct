from config.settings import Settings
from services.sheets_service import GoogleSheetsService

settings = Settings.from_env()
sheets = GoogleSheetsService(settings)
all_rows = sheets.get_all_rows(only_entrada_saida=False)
mvv_rows = [r for r in all_rows if str(r.raw.get('TIPO', '')).strip().upper() == 'MVV']

print(f"Total MVV rows: {len(mvv_rows)}")
for r in sorted(mvv_rows, key=lambda x: x.row_number, reverse=True):
    rep = r.raw.get("REPASSE", "")
    print(f"Row {r.row_number:4d} | {r.data_mov} | {r.importancia:>10} | {r.id_interno:<14} | DocSoma: {r.doc_soma:<10} | Desc: {r.descricao} | REPASSE: '{rep}'")
