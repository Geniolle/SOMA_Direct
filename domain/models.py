from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class TipoMovimento(str, Enum):
    ENTRADA = "Entrada"
    SAIDA = "Saída"
    TRANSFERENCIA = "Transferência"
    CARTAO = "Cartão"
    MVV = "MVV"
    OUTRO = "Outro"


def is_entrada_ou_saida(val: Any) -> bool:
    """Retorna True apenas se o tipo for estritamente Entrada ou Saída."""
    if isinstance(val, TipoMovimento):
        return val in (TipoMovimento.ENTRADA, TipoMovimento.SAIDA)
    norm = norm_basic(val)
    return norm in ("entrada", "ent", "in", "saida", "saída", "out")


def normalize_str(s: Any) -> str:
    if s is None:
        return ""
    txt = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in txt if not unicodedata.combining(c)).strip()


def norm_basic(s: Any) -> str:
    s = str(s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.split())


def normalize_document_value(val: Any) -> str:
    s = str(val or "").strip()
    if re.fullmatch(r"[+-]?\d+\.0+", s):
        return s.split(".", 1)[0]
    return s


def format_amount_for_input(value: Any) -> str:
    """Formata um montante para formato decimal padrão 2 casas com vírgula."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        amount = value
    else:
        text = str(value or "").strip().replace(" ", "")
        if not text:
            return ""
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError):
            return str(value or "").strip()
    return format(amount.quantize(Decimal("0.01")), "f").replace(".", ",")


def clean_amount_for_comparison(val: Any) -> str:
    """Limpa símbolos de moeda e garante 2 casas decimais com vírgula."""
    s = re.sub(r"[^\d,.-]", "", str(val or ""))
    return format_amount_for_input(s)


def normalize_date_str(value: Any) -> str:
    """Normaliza datas para o formato DD/MM/YYYY."""
    s = str(value or "").strip()
    if not s:
        return ""
    s = s.split()[0]
    parts = re.split(r"[/.-]", s)
    if len(parts) == 3:
        try:
            if len(parts[2]) == 4:
                d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                return f"{d:02d}/{m:02d}/{y:04d}"
            elif len(parts[0]) == 4:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                return f"{d:02d}/{m:02d}/{y:04d}"
        except ValueError:
            pass
    return s


def strip_suffix_n(text: Any) -> str:
    """Remove sufixo sequencial como ' N001', ' N002', etc."""
    return re.sub(r"\s+N\d+$", "", str(text or "").strip(), flags=re.IGNORECASE).strip()


def extract_suffix_n(text: Any) -> Optional[int]:
    """Extrai o número sequencial de ' N001', ' N002', etc., ou None se ausente."""
    m = re.search(r"\bN(\d+)\b", str(text or ""), flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def strip_date_prefix(text: Any) -> str:
    """Remove prefixos de data como '30/11/2025-' ou '01/01/2024 - '."""
    return re.sub(r"^\d{2}/\d{2}/\d{4}\s*[-–]\s*", "", str(text or "")).strip()


def clean_caixa(caixa: Any) -> str:
    """Remove sufixos entre colchetes como [CONTA CORRENTE] e normaliza."""
    c = re.sub(r"\[.*?\]", "", str(caixa or "").strip()).strip()
    return norm_basic(c)


def validate_dados_doc(
    dados_doc: str,
    sheet_caixa: str,
    sheet_forma: str,
) -> Tuple[bool, Optional[str]]:
    """Valida se o caixa e forma de pagamento no DADOS DOC batem com a planilha."""
    if not dados_doc or not dados_doc.strip():
        return False, "DADOS DOC vazio no portal SOMA"

    parts = [p.strip() for p in dados_doc.split(",") if p.strip()]
    if len(parts) < 3:
        return False, f"Formato inesperado em DADOS DOC: '{dados_doc}'"

    site_caixa = parts[1]
    site_forma_resto = parts[2]

    norm_sheet_c = clean_caixa(sheet_caixa)
    norm_site_c = clean_caixa(site_caixa)

    if norm_sheet_c and norm_site_c:
        if norm_sheet_c != norm_site_c and norm_sheet_c not in norm_site_c and norm_site_c not in norm_sheet_c:
            return False, f"CAIXA divergente no DADOS DOC: portal='{site_caixa}' != sheet='{sheet_caixa}'"

    norm_sheet_f = norm_basic(sheet_forma)
    norm_site_f = norm_basic(site_forma_resto)

    if norm_sheet_f and norm_site_f:
        if norm_sheet_f not in norm_site_f:
            transfer_synonyms = {"transferencia bancaria", "transferencia", "deposito"}
            if norm_sheet_f in transfer_synonyms and any(s in norm_site_f for s in transfer_synonyms):
                pass
            else:
                return False, f"FORMA DE PAGAMENTO divergente no DADOS DOC: portal='{site_forma_resto}' != sheet='{sheet_forma}'"

    return True, None



@dataclass
class ContaOrdemRow:
    row_number: int
    data_mov: str
    descricao: str
    importancia: str
    doc_soma: str
    tipo: TipoMovimento
    plano_conta: str = ""
    centro_custo: str = ""
    descricao_soma: str = ""
    forma_pagamento: str = ""
    caixa: str = ""
    caixa_saida: str = ""
    id_interno: str = ""
    status: str = ""
    dados_doc: str = ""
    auditoria: str = ""
    timestamp: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, row_number: int, raw: Dict[str, Any]) -> "ContaOrdemRow":
        raw_tipo = normalize_str(raw.get("TIPO") or raw.get("tipo") or "")
        norm_t = norm_basic(raw_tipo)
        if norm_t in ("saida", "saída", "out"):
            tipo = TipoMovimento.SAIDA
        elif norm_t in ("entrada", "ent", "in"):
            tipo = TipoMovimento.ENTRADA
        elif "transfer" in norm_t:
            tipo = TipoMovimento.TRANSFERENCIA
        elif "cartao" in norm_t:
            tipo = TipoMovimento.CARTAO
        elif "mvv" in norm_t:
            tipo = TipoMovimento.MVV
        else:
            tipo = TipoMovimento.OUTRO

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
            auditoria=str(raw.get("AUDITORIA") or raw.get("auditoria") or "").strip(),
            timestamp=str(raw.get("TIMESTAMP") or raw.get("timestamp") or "").strip(),
            raw=raw,
        )


@dataclass
class SomaSearchResult:
    codigo: str
    tipo: str
    descricao: str
    valor: str
    data: str
    status: str = ""
    baixa: str = ""


@dataclass
class AuditOutcome:
    analyzed: bool
    confirmed: bool = False
    corrected: bool = False
    inconsistent: bool = False
    technical_error: bool = False
    inconsistencies: List[str] = field(default_factory=list)
    new_doc: Optional[str] = None
    new_desc: Optional[str] = None
    dados_doc: str = ""


@dataclass
class OperationOutcome:
    success: bool
    doc_id: str
    tipo: str
    row_number: int
    elapsed_ms: int
    dados_doc: str = ""
    error_message: str = ""


@dataclass
class CascadeAuditOutcome:
    """Resultado da conciliação em cascata (4 níveis hierárquicos)."""
    confirmed: bool = False
    corrected: bool = False
    level_resolved: int = 0  # 1: DOC direto, 2: Lote Data, 3: Semântico, 4: Origem
    auditoria: str = ""
    new_doc: Optional[str] = None
    new_desc: Optional[str] = None
    dados_doc: str = ""
    notes: str = ""

