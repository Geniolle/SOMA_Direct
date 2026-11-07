from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
import re

settings = Settings.from_env()
http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
auth = SomaAuthenticator(settings, http)
auth.login()
base = settings.site_base_url.rstrip("/")

for query in ["REPASSE", "DÍZIMO", "COVV", "MISSÕES", "NOVAS OBRAS", "LIVRARIA 3%"]:
    p = {
        "pesquisa": query,
        "filtro": "descricao",
        "id_inst": settings.institution_id,
        "tipo": "0",  # Saidas
        "v": "0",  # all dates
        "s": "2",
        "t_d": "1",
        "cc": "-1",
        "c": "",
    }
    r = http.post_ajax(f"{base}/sys/post/buscarEntradasSaidas.php", data=p)
    items = []
    for rw in re.findall(r'<tr\b[^>]*>(.*?)</tr>', r.text, re.DOTALL):
        cells = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', rw, re.DOTALL)]
        if len(cells) >= 9 and cells[2].lower() != "codigo":
            items.append((cells[2], cells[4], cells[5], cells[6]))
    print(f"Query '{query}': {len(items)} saidas encontradas")
    for doc, desc, val, dt in items[:8]:
        print(f"   Doc {doc} | {dt} | {val:>10} € | {desc}")
