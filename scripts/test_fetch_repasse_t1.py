from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
import re

settings = Settings.from_env()
http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
auth = SomaAuthenticator(settings, http)
if not auth.login():
    print("Login failed")
    exit(1)

base = settings.site_base_url.rstrip("/")
resp_t1 = http.get(f"{base}/sys/relatorios/relatorios_repasse.php", params={
    "i": settings.institution_id,
    "t": "1",
    "a_s": "2024",
    "p": "undefined",
    "t_i": "undefined",
    "end": "undefined",
    "s": "undefined",
    "t_comp": "undefined",
})

with open("scratch_repasse_t1_2024.html", "w", encoding="utf-8") as f:
    f.write(resp_t1.text)

print("Saved scratch_repasse_t1_2024.html, len:", len(resp_t1.text))
