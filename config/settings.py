from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from config.paths import DEFAULT_ENV_PATH, resolve_project_path


@dataclass(frozen=True)
class Settings:
    # Portal SOMA
    site_user: str
    site_password: str
    site_login_url: str = "https://verbodavida.info/apps/index.php"
    site_base_url: str = "https://verbodavida.info/IVV/"
    institution_id: str = "270"  # BRAGA - PORTUGAL
    
    # Google Sheets
    google_credentials_path: str = str(resolve_project_path("credentials/sheets-service-account.json"))
    spreadsheet_url: str = "https://docs.google.com/spreadsheets/d/1poVWJGSBb13_2S1YKEzvFmkB9Ru0ZVzfQ0OEcMkfOZw/edit"
    sheet_contaordem: str = "CONTAORDEM"
    sheet_caixas: str = "GERENCIAR CAIXAS"
    sheet_soma: str = "SOMA"
    sheet_repasse: str = "T_REPASSE"
    institution_name: str = "BRAGA - PORTUGAL"
    app_verbo_cafe_spreadsheet_url: str = "https://docs.google.com/spreadsheets/d/11sUHhTzKaV21uX_FpBOEnJxNpFjUn6EHEiU79Pe3jXU/edit"
    
    # Execução
    timeout_seconds: int = 25
    user_job_id: str = "USERJOB"
    verify_tls: bool = True
    claim_stale_seconds: int = 900
    reconciliation_interval_seconds: int = 3600

    # Pós-processos (portados do legado C:\workspace\SOMA): saldos de Caixas/Bancos
    # e relatório da sheet SOMA. Default False até serem validados em produção.
    run_caixas_bancos: bool = False
    run_soma_sheet: bool = False

    @classmethod
    def from_env(cls, env_path: Optional[str | Path] = None) -> "Settings":
        resolved_env_path = (
            resolve_project_path(env_path) if env_path is not None else DEFAULT_ENV_PATH
        )
        if resolved_env_path.is_file():
            load_dotenv(dotenv_path=resolved_env_path, override=True)
            
        return cls(
            site_user=os.getenv("SITE_USER", ""),
            site_password=os.getenv("SITE_PASSWORD", ""),
            site_login_url=os.getenv("SITE_LOGIN_URL", "https://verbodavida.info/apps/index.php"),
            site_base_url=os.getenv("SITE_HOME_URL", "https://verbodavida.info/IVV/"),
            institution_id=os.getenv("INSTITUTION_ID", "270"),
            google_credentials_path=str(
                resolve_project_path(
                    os.getenv(
                        "GOOGLE_CREDENTIALS_PATH",
                        "credentials/sheets-service-account.json",
                    )
                )
            ),
            spreadsheet_url=os.getenv("SPREADSHEET_URL", "https://docs.google.com/spreadsheets/d/1poVWJGSBb13_2S1YKEzvFmkB9Ru0ZVzfQ0OEcMkfOZw/edit"),
            sheet_contaordem=os.getenv("SHEET_CONTAORDEM", "CONTAORDEM"),
            sheet_caixas=os.getenv("SHEET_CAIXAS", "GERENCIAR CAIXAS"),
            sheet_soma=os.getenv("SHEET_SOMA", "SOMA"),
            sheet_repasse=os.getenv("SHEET_REPASSE", "T_REPASSE"),
            institution_name=os.getenv("INSTITUTION_NAME", "BRAGA - PORTUGAL"),
            app_verbo_cafe_spreadsheet_url=os.getenv(
                "APP_VERBO_CAFE_SPREADSHEET_URL",
                "https://docs.google.com/spreadsheets/d/11sUHhTzKaV21uX_FpBOEnJxNpFjUn6EHEiU79Pe3jXU/edit",
            ),
            timeout_seconds=int(os.getenv("TIMEOUT_SECONDS", "25") or 25),
            user_job_id=os.getenv("IDUSER", "USERJOB") or "USERJOB",
            verify_tls=os.getenv("VERIFY_TLS", "true").strip().lower() not in ("0", "false", "no"),
            claim_stale_seconds=int(os.getenv("CLAIM_STALE_SECONDS", "900") or 900),
            reconciliation_interval_seconds=int(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "3600") or 3600),
            run_caixas_bancos=os.getenv("RUN_CAIXAS_BANCOS", "false").strip().lower() in ("1", "true", "yes"),
            run_soma_sheet=os.getenv("RUN_SOMA_SHEET", "false").strip().lower() in ("1", "true", "yes"),
        )
