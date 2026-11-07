from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
import re

settings = Settings.from_env()
http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
auth = SomaAuthenticator(settings, http)
auth.login()
base = settings.site_base_url.rstrip("/")

def parse_table(html):
    results = []
    for rw in re.findall(r'<tr\b[^>]*>(.*?)</tr>', html, re.DOTALL):
        cells = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', rw, re.DOTALL)]
        if len(cells) >= 9:
            codigo = cells[2]
            if codigo.lower() == "codigo":
                continue
            results.append({
                "codigo": codigo,
                "tipo": cells[3],
                "descricao": cells[4],
                "valor": cells[5],
                "data": cells[6],
                "status": cells[7],
                "baixa": cells[8],
            })
    return results

# 1. Search on 18/03/2024
for td in ["0", "1"]:
    payload = {
        "pesquisa": "",
        "filtro": "descricao",
        "id_inst": settings.institution_id,
        "tipo": "2",
        "v": "1",
        "s": "2",
        "t_d": td,
        "cc": "-1",
        "c": "",
        "i": "18/03/2024",
        "f": "18/03/2024",
    }
    r = http.post_ajax(f"{base}/sys/post/buscarEntradasSaidas.php", data=payload)
    items = parse_table(r.text)
    print(f"buscarEntradasSaidas 18/03/2024 (t_d={td}): {len(items)} items")
    for it in items:
        print("  ", it)

# 2. Search description containing REPASSE or DIZIMO across March 2024
payload = {
    "pesquisa": "REPASSE",
    "filtro": "descricao",
    "id_inst": settings.institution_id,
    "tipo": "2",
    "v": "1",
    "s": "2",
    "t_d": "0",
    "cc": "-1",
    "c": "",
    "i": "01/01/2024",
    "f": "31/12/2024",
}
r = http.post_ajax(f"{base}/sys/post/buscarEntradasSaidas.php", data=payload)
items = parse_table(r.text)
print(f"\nbuscarEntradasSaidas 2024 pesquisa='REPASSE': {len(items)} items")
for it in items[:10]:
    print("  ", it)
