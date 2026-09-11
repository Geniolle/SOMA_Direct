from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.audit_service import AuditService
from services.sheets_service import GoogleSheetsService


def main() -> None:
    settings = Settings.from_env()
    http = ResilientSession(timeout=settings.timeout_seconds)
    SomaAuthenticator(settings, http).login()
    result = AuditService(settings, http, GoogleSheetsService(settings)).search_by_codigo(sys.argv[1])
    print(result)


if __name__ == "__main__":
    main()
