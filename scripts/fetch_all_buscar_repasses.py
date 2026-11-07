from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
import re

settings = Settings.from_env()
http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
auth = SomaAuthenticator(settings, http)
auth.login()
base = settings.site_base_url.rstrip("/")

for ano in [2024, 2025, 2026]:
    for s_val in ["0", "1"]:
        st_label = "Nao Conferido" if s_val == "0" else "Conferido"
        resp = http.post_ajax(f"{base}/sys/post/buscarRepasses.php", data={
            "p_i": f"01/01/{ano}",
            "p_f": f"31/12/{ano}",
            "id_inst": settings.institution_id,
            "s": s_val,
            "f": "0",
        })
        rows = []
        for m in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", resp.text, re.DOTALL | re.IGNORECASE):
            cells = [" ".join(re.sub(r"<[^>]+>", " ", c).split()) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", m.group(1), re.DOTALL | re.IGNORECASE)]
            if len(cells) > 4 and "DESCRICAO" not in cells[2]:
                rows.append(cells)
        print(f"Ano {ano} | s={s_val} ({st_label}): {len(rows)} repasses")
        for r in rows[:5]:
            print(f"   {r}")
