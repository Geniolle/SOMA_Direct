from __future__ import annotations

import logging
import re
from typing import Optional
from config.settings import Settings
from core.http_session import ResilientSession

logger = logging.getLogger("soma_direct.auth")


class SomaAuthenticator:
    """Autenticação direta no Portal SOMA (login POST + cookie PHPSESSID)."""

    def __init__(self, settings: Settings, http_session: ResilientSession):
        self.settings = settings
        self.http = http_session
        self.is_authenticated = False

    def login(self) -> bool:
        logger.info(f"Iniciando autenticação direta para {self.settings.site_user}...")
        
        # 1. Login via buscaUser.php
        busca_url = "https://verbodavida.info/apps/sys/buscaUser.php"
        r1 = self.http.post_ajax(busca_url, data={
            "login": self.settings.site_user,
            "senha": self.settings.site_password
        })
        
        if r1.text.strip() not in ("1", "true") and not r1.text.strip().isdigit():
            logger.error(f"Falha no buscaUser: {r1.text[:200]}")
            return False
            
        logger.info("buscaUser autenticado com sucesso.")

        # 2. Inicialização do módulo IVV (SOMA) via redirecionar.php
        redir_url = "https://verbodavida.info/apps/sys/redirecionar.php"
        r2 = self.http.post_ajax(redir_url, data={
            "email": self.settings.site_user,
            "id": "285",
            "data": "ivv"
        })
        
        if r2.text.strip() != "1":
            logger.error(f"Falha ao autorizar módulo IVV: {r2.text[:200]}")
            return False
            
        logger.info("Autorização do módulo IVV concedida.")

        # 3. Confirmação do acesso à raiz do módulo IVV/
        ivv_url = self.settings.site_base_url.rstrip("/") + "/"
        r3 = self.http.get(ivv_url, allow_redirects=True)
        
        if "Entradas/Saídas" in r3.text or "Caixas/Bancos" in r3.text or "SOMA" in r3.text:
            self.is_authenticated = True
            logger.info("Autenticação direta no SOMA realizada com sucesso! (Sessão HTTP ativa)")
            return True
            
        if "PHPSESSID" in self.http.session.cookies.get_dict():
            self.is_authenticated = True
            logger.info("Cookie PHPSESSID presente na sessão ativa.")
            return True

        logger.error("Não foi possível confirmar o login no SOMA.")
        return False
