from config.settings import Settings
from services.sheets_service import GoogleSheetsService

settings = Settings.from_env()
sheets = GoogleSheetsService(settings)
rows = sheets.get_all_rows("CONTAORDEM")

mvv_rows = []
for r in rows:
    tipo = str(r.raw.get("TIPO", "")).strip()
    if tipo == "MVV":
        mvv_rows.append(r)

print(f"Total MVV rows: {len(mvv_rows)}")
for r in mvv_rows:
    repasse = r.raw.get("REPASSE", "")
    print(f"Row {r.row_number:4d} | {r.data_mov} | {r.importancia:>10} | {r.id_interno:<14} | {r.doc_soma:<10} | {r.descricao} | REPASSE='{repasse}'")
