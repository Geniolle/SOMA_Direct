from __future__ import annotations

import logging
from typing import Optional
from domain.models import TRANSFER_DOC_MARKER, ContaOrdemRow, TipoMovimento
from services.soma_api_service import SomaApiService

logger = logging.getLogger("soma_direct.duplicate_checker")


class DuplicateChecker:
    """Verificador de duplicidade antes do lancamento."""

    def __init__(self, api_service: SomaApiService):
        self.api = api_service

    def check_exists(self, row: ContaOrdemRow) -> Optional[str]:
        """Retorna o DOC ID existente se o registro ja tiver sido lancado no SOMA, senao None."""
        desc = row.descricao_soma or row.descricao
        valor = row.importancia
        data_mov = row.data_mov

        if row.tipo == TipoMovimento.TRANSFERENCIA:
            _, exact = self.api.find_transfers(row)
            if len(exact) == 1:
                existing_id = exact[0].transfer_id
                logger.info(f"Linha {row.row_number}: Transferencia ja existente no SOMA (ID {existing_id}).")
                return TRANSFER_DOC_MARKER
        else:
            tipo_code = "0" if row.tipo == TipoMovimento.SAIDA else "1"
            existing_id = self.api._find_doc_id(tipo=tipo_code, descricao=desc, valor=valor, data_mov=data_mov)
            if existing_id:
                logger.info(f"Linha {row.row_number}: Documento ja existente no SOMA (ID {existing_id}).")
                return existing_id

        return None
