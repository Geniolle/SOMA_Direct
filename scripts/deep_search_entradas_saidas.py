from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
import re

settings = Settings.from_env()
http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
auth = SomaAuthenticator(settings, http)
auth.login()
base = settings.site_base_url.rstrip("/")

def parse_items(html):
    res = []
    for rw in re.findall(r'<tr\b[^>]*>(.*?)</tr>', html, re.DOTALL):
        cells = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', rw, re.DOTALL)]
        if len(cells) >= 9 and cells[2].lower() != "codigo":
            res.append({
                "codigo": cells[2],
                "tipo": cells[3],
                "descricao": cells[4],
                "valor": cells[5],
                "data": cells[6],
                "status": cells[7],
                "baixa": cells[8],
            })
    return res

# 1. Search by CC = '10.20.01 - REPASSE MVV' (Let's find the ID of CC 10.20.01)
# First let's inspect the options of Centro de Custo in ?mod=ivv&exec=entradas_saidas
resp_page = http.get(f"{base}?mod=ivv&exec=entradas_saidas")
cc_options = re.findall(r'<option\b[^>]*value=["\']([^"\']*)["\'][^>]*>(.*?)</option>', resp_page.text)
cc_mvv_id = None
for val, text in cc_options:
    if "REPASSE" in text.upper() or "MVV" in text.upper():
        print(f"Found Centro de Custo: val='{val}', text='{text.strip()}'")
        cc_mvv_id = val

if cc_mvv_id:
    # Search all in this Centro de Custo
    payload = {
        "pesquisa": "",
        "filtro": "descricao",
        "id_inst": settings.institution_id,
        "tipo": "2",
        "v": "0",  # all dates
        "s": "2",
        "t_d": "1",
        "cc": cc_mvv_id,
        "c": "",
    }
    r = http.post_ajax(f"{base}/sys/post/buscarEntradasSaidas.php", data=payload)
    items = parse_items(r.text)
    print(f"\nTotal lancamentos in Centro de Custo {cc_mvv_id}: {len(items)}")
    for it in items:
        print("  ", it)

# 2. Search for the specific global amounts in buscarEntradasSaidas
global_amounts = [
    "643,74", "664,48", "701,45", "644,76", "553,52",
    "494,54", "949,12", "652,62", "551,50", "571,25",
    "38,25", "681,47", "921,57", "523,63"
]

print("\n--- Searching global amounts with filtro=valor ---")
for amt in global_amounts:
    p = {
        "pesquisa": amt,
        "filtro": "valor",
        "id_inst": settings.institution_id,
        "tipo": "2", "v": "0", "s": "2", "t_d": "1", "cc": "-1", "c": ""
    }
    r = http.post_ajax(f"{base}/sys/post/buscarEntradasSaidas.php", data=p)
    items = parse_items(r.text)
    if items:
        print(f"Amount {amt}: FOUND {len(items)} items:")
        for it in items:
            print("   ", it)
    else:
        print(f"Amount {amt}: NOT found")

# 3. Search for divided amounts (rubricas)
divided_amounts = [
    "442,98", "88,60", "44,30",
    "426,82", "85,36", "42,69", "3,51",
    "467,63", "93,53", "46,76",
    "429,84", "85,97", "42,98",
    "453,36", "89,95", "44,97", "3,24",
    "612,99", "121,28", "60,64", "5,38",
    "350,42", "65,88", "32,94", "1,91",
    "518,21", "101,80", "50,90", "3,54",
]

print("\n--- Searching divided amounts with filtro=valor ---")
found_divided = []
for amt in divided_amounts:
    p = {
        "pesquisa": amt,
        "filtro": "valor",
        "id_inst": settings.institution_id,
        "tipo": "2", "v": "0", "s": "2", "t_d": "1", "cc": "-1", "c": ""
    }
    r = http.post_ajax(f"{base}/sys/post/buscarEntradasSaidas.php", data=p)
    items = parse_items(r.text)
    if items:
        found_divided.append((amt, items))
        print(f"Divided {amt}: FOUND {len(items)} items:")
        for it in items:
            print("   ", it)

if not found_divided:
    print("NONE of the divided amounts were found in Entradas/Saidas!")
