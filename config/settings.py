from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass(frozen=True)
class Settings:
    # Portal SOMA
    site_user: str
    site_password: str
    site_login_url: str = "https://verbodavida.info/apps/index.php"
    site_base_url: str = "https://verbodavida.info/IVV/"
    institution_id: str = "270"  # BRAGA - PORTUGAL
    
    # Google Sheets
    google_credentials_path: str = "C:/workspace/Tesouraria-SOMA/credentials/sheets-service-account.json"
    spreadsheet_url: str = "https://docs.google.com/spreadsheets/d/1poVWJGSBb13_2S1YKEzvFmkB9Ru0ZVzfQ0OEcMkfOZw/edit"
    sheet_contaordem: str = "CONTAORDEM"
    sheet_caixas: str = "GERENCIAR CAIXAS"
    sheet_soma: str = "SOMA"
    
    # Execução
    timeout_seconds: int = 25
    user_job_id: str = "USERJOB"
    
    @classmethod
    def from_env(cls, env_path: Optional[str] = None) -> "Settings":
        if env_path and Path(env_path).exists():
            load_dotenv(dotenv_path=env_path, override=True)
        elif Path("C:/workspace/SOMA/.env").exists():
            load_dotenv(dotenv_path="C:/workspace/SOMA/.env", override=False)
            
        return cls(
            site_user=os.getenv("SITE_USER", "familialopesemportugal@gmail.com"),
            site_password=os.getenv("SITE_PASSWORD", "P@1internet"),
            site_login_url=os.getenv("SITE_LOGIN_URL", "https://verbodavida.info/apps/index.php"),
            site_base_url=os.getenv("SITE_HOME_URL", "https://verbodavida.info/IVV/"),
            institution_id=os.getenv("INSTITUTION_ID", "270"),
            google_credentials_path=os.getenv("GOOGLE_CREDENTIALS_PATH", "C:/workspace/Tesouraria-SOMA/credentials/sheets-service-account.json"),
            spreadsheet_url=os.getenv("SPREADSHEET_URL", "https://docs.google.com/spreadsheets/d/1poVWJGSBb13_2S1YKEzvFmkB9Ru0ZVzfQ0OEcMkfOZw/edit"),
            sheet_contaordem=os.getenv("SHEET_CONTAORDEM", "CONTAORDEM"),
            sheet_caixas=os.getenv("SHEET_CAIXAS", "GERENCIAR CAIXAS"),
            sheet_soma=os.getenv("SHEET_SOMA", "SOMA"),
            timeout_seconds=int(os.getenv("TIMEOUT_SECONDS", "25") or 25),
            user_job_id=os.getenv("IDUSER", "USERJOB") or "USERJOB",
        )
