from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class TipoMovimento(str, Enum):
    ENTRADA = "Entrada"
    SAIDA = "Saída"
    TRANSFERENCIA = "Transferência"


def normalize_str(s: Any) -> str:
    if s is None:
        return ""
    txt = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in txt if not unicodedata.combining(c)).strip()


@dataclass
class ContaOrdemRow:
    row_number: int
    data_mov: str
    descricao: str
    importancia: str
    doc_soma: str
    tipo: TipoMovimento
    plano_conta: str
    centro_custo: str
    descricao_soma: str
    forma_pagamento: str
    caixa: str
    caixa_saida: str = ""
    id_interno: str = ""
    status: str = ""
    dados_doc: str = ""
    timestamp: str = ""
    raw: Dict[str, Any] = None

    @classmethod
    def from_dict(cls, row_number: int, raw: Dict[str, Any]) -> "ContaOrdemRow":
        raw_tipo = normalize_str(raw.get("TIPO") or raw.get("tipo") or "")
        if "saida" in raw_tipo.lower():
            tipo = TipoMovimento.SAIDA
        elif "transfer" in raw_tipo.lower():
            tipo = TipoMovimento.TRANSFERENCIA
        else:
            tipo = TipoMovimento.ENTRADA

        return cls(
            row_number=row_number,
            data_mov=str(raw.get("DATA MOV.") or raw.get("data_mov") or "").strip(),
            descricao=str(raw.get("DESCRIÇÃO") or raw.get("descricao") or "").strip(),
            importancia=str(raw.get("IMPORTÂNCIA") or raw.get("importancia") or "").strip(),
            doc_soma=str(raw.get("DOC. SOMA") or raw.get("doc_soma") or "").strip(),
            tipo=tipo,
            plano_conta=str(raw.get("PLANO DE CONTA") or raw.get("plano_conta") or "").strip(),
            centro_custo=str(raw.get("CENTRO DE CUSTO") or raw.get("centro_custo") or "").strip(),
            descricao_soma=str(raw.get("DESCRIÇÃO SOMA") or raw.get("descricao_soma") or "").strip(),
            forma_pagamento=str(raw.get("FORMA DE PAGAMENTO") or raw.get("forma_pagamento") or "").strip(),
            caixa=str(raw.get("CAIXA") or raw.get("caixa") or "").strip(),
            caixa_saida=str(raw.get("CAIXA SAIDA") or raw.get("caixa_saida") or "").strip(),
            id_interno=str(raw.get("ID_INTERNO") or raw.get("id_interno") or "").strip(),
            status=str(raw.get("STATUS") or raw.get("status") or "").strip(),
            dados_doc=str(raw.get("DADOS DOC") or raw.get("dados_doc") or "").strip(),
            timestamp=str(raw.get("TIMESTAMP") or raw.get("timestamp") or "").strip(),
            raw=raw,
        )


@dataclass
class OperationOutcome:
    success: bool
    doc_id: str
    tipo: str
    row_number: int
    elapsed_ms: int
    dados_doc: str = ""
    error_message: str = ""
