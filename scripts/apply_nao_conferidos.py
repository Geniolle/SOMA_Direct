from config.settings import Settings
from services.sheets_service import GoogleSheetsService

settings = Settings.from_env()
sheets = GoogleSheetsService(settings)

rows_to_update = [4270, 3399, 3282, 3223, 3222, 2203]
batch_data = [{"range": f"Z{row_idx}", "values": [["Não conferido"]]} for row_idx in rows_to_update]

sheets._ws.batch_update(batch_data)
print("Batch update complete for rows:", rows_to_update)

for r in rows_to_update:
    vals = sheets._ws.row_values(r)
    col_z = vals[25] if len(vals) > 25 else "(vazio)"
    print(f"Row {r:4d} | Data: {vals[0]} | Valor: {vals[3]:>8} € | REPASSE (Z): '{col_z}'")
