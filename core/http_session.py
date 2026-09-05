from __future__ import annotations

import logging
import requests
import urllib3
from typing import Any, Dict, Optional

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("soma_direct.http")


class ResilientSession:
    """Sessão HTTP persistente para o portal SOMA com retries e headers adequados."""

    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-PT,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    AJAX_HEADERS = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }

    def __init__(self, timeout: int = 35, max_retries: int = 3):
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        self.timeout = timeout
        self.max_retries = max_retries

    def get(self, url: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", False)
        retries = kwargs.pop("retries", self.max_retries)
        for attempt in range(1, retries + 1):
            try:
                return self.session.get(url, params=params, **kwargs)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt == retries:
                    logger.error("Falha final GET %s apos %d tentativas: %s", url, retries, e)
                    raise
                wait = attempt * 2
                logger.warning("Erro de conexao/timeout no GET %s (tentativa %d/%d). Aguardando %ds: %s", url, attempt, retries, wait, e)
                import time
                time.sleep(wait)

    def post(self, url: str, data: Optional[Any] = None, files: Optional[Any] = None, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", False)
        retries = kwargs.pop("retries", self.max_retries)
        for attempt in range(1, retries + 1):
            try:
                return self.session.post(url, data=data, files=files, **kwargs)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt == retries:
                    logger.error("Falha final POST %s apos %d tentativas: %s", url, retries, e)
                    raise
                wait = attempt * 2
                logger.warning("Erro de conexao/timeout no POST %s (tentativa %d/%d). Aguardando %ds: %s", url, attempt, retries, wait, e)
                import time
                time.sleep(wait)

    def post_ajax(self, url: str, data: Optional[Any] = None, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {})
        merged_headers = {**self.AJAX_HEADERS, **headers}
        return self.post(url, data=data, headers=merged_headers, **kwargs)
