from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
import re

settings = Settings.from_env()
http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
auth = SomaAuthenticator(settings, http)
auth.login()
base = settings.site_base_url.rstrip("/")

# Let's fetch Balancete for Fevereiro/2024: 01/02/2024 a 29/02/2024
url = f"{base}/sys/relatorios/relatorios_balancete.php"
resp = http.get(url, params={
    "i": settings.institution_id,
    "c": "01/02/2024",
    "f": "29/02/2024",
    "t": "0",
    "p": "5",
    "id_cc": "null",
    "id_c": "null",
    "id_m": "2",
})

with open("scratch_balancete_fev_2024.html", "w", encoding="utf-8") as f:
    f.write(resp.text)

print("Saved scratch_balancete_fev_2024.html, len:", len(resp.text))

# Let's parse tables in the balancete
tables = re.findall(r'<table\b[^>]*>(.*?)</table>', resp.text, re.DOTALL | re.IGNORECASE)
print("Total tables:", len(tables))
for t_idx, table_html in enumerate(tables):
    rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)
    print(f"\n--- TABLE {t_idx} ({len(rows)} rows) ---")
    for r_idx, row_html in enumerate(rows):
        cells = re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', row_html, re.DOTALL | re.IGNORECASE)
        clean_cells = [' '.join(re.sub(r'<[^>]+>', ' ', c).split()) for c in cells if c]
        row_str = ' '.join(clean_cells)
        if any(term in row_str.upper() for term in ["REPASSE", "DÍZIMO", "DIZIMO", "COVV", "MISSÕES", "MISSOES", "NOVAS OBRAS", "SAÍDA", "SAIDA", "TOTAL"]):
            print(f"Row {r_idx:2d}: {clean_cells}")
