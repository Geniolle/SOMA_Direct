import re
import sys
from html import unescape
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession


def clean(text: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))).strip()


def main() -> int:
    doc = sys.argv[1]
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds, verify_tls=settings.verify_tls)
    if not SomaAuthenticator(settings, http).login():
        raise SystemExit("Falha no login do SOMA")
    url = f"{settings.site_base_url.rstrip('/')}/?mod=ivv&exec=entradas_saidas_dados&ID={doc}"
    response = http.get(url)
    print(f"HTTP\t{response.status_code}")
    for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", response.text, re.IGNORECASE):
        name = re.search(r'\bname=["\']([^"\']+)', tag, re.IGNORECASE)
        ident = re.search(r'\bid=["\']([^"\']+)', tag, re.IGNORECASE)
        value = re.search(r'\bvalue=["\']([^"\']*)', tag, re.IGNORECASE)
        if name or ident:
            print("FIELD\t" + "\t".join([
                f"name={name.group(1) if name else ''}",
                f"id={ident.group(1) if ident else ''}",
                f"value={unescape(value.group(1)) if value else ''}",
            ]))
    for select_match in re.finditer(r"<select\b([^>]*)>(.*?)</select>", response.text, re.IGNORECASE | re.DOTALL):
        attrs, body = select_match.groups()
        name = re.search(r'\bname=["\']([^"\']+)', attrs, re.IGNORECASE)
        ident = re.search(r'\bid=["\']([^"\']+)', attrs, re.IGNORECASE)
        selected = ""
        selected_text = ""
        for option_attrs, option_text in re.findall(r"<option\b([^>]*)>(.*?)</option>", body, re.IGNORECASE | re.DOTALL):
            if "selected" in option_attrs.lower():
                value = re.search(r'\bvalue=["\']([^"\']*)', option_attrs, re.IGNORECASE)
                selected = unescape(value.group(1)) if value else ""
                selected_text = clean(option_text)
                break
        print("SELECT\t" + "\t".join([
            f"name={name.group(1) if name else ''}",
            f"id={ident.group(1) if ident else ''}",
            f"selected={selected}",
            f"text={selected_text}",
        ]))
    for form in re.findall(r"<form\b[^>]*>(.*?)</form>", response.text, re.IGNORECASE | re.DOTALL):
        header = response.text[max(0, response.text.find(form) - 300): response.text.find(form)]
        print("FORM_CONTEXT\t" + clean(header)[-500:])
        print("FORM_TEXT\t" + clean(form)[:1000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
