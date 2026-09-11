#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Ronda_Ficheiro.py
=================
Motor de Auditoria Tesouraria SOMA - Port de AuditoriaTesouraria_v57 para Python.

Funcionalidades de Especialista:
- Execução para um mês específico (ex: Setembro de 2026) ou em lote mês a mês (desde Janeiro de 2024).
- Carregamento e cache único em memória de todas as sheets (alta performance, sem esgotar cota do Google Sheets).
- Auditoria completa: Balancete, Cobertura Origens x Destino com exclusão simétrica por ID_INTERNO,
  MVV mensal, Ciclos de Cartão de Crédito com Carry-Forward e detecção histórica por subset-sum,
  validação autoritativa de DOC.SOMA real e fallback sequencial diário N001..N999.
- Relatório detalhado dos erros encontrados: balancete, divergências, falhas de cobertura, pendências.
- Opção para gravar na sheet T_AUDIT (com formatação, cores e balancete) ou executar em modo simulação/relatório.
"""

from __future__ import annotations

import argparse
import calendar
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import gspread
from dotenv import load_dotenv

# Reconfigurar stdout/stderr para UTF-8 no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Carregar variáveis de ambiente
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("Ronda_Ficheiro")

TOL = 0.01

CONFIG_EXT = {
    "FINANCEIRO": {
        "spreadsheetId": "11sUHhTzKaV21uX_FpBOEnJxNpFjUn6EHEiU79Pe3jXU",
        "sheetName": "Financeiro",
        "valorAliases": ["MONTANTE"]
    },
    "VC_VENDAS": {
        "spreadsheetId": "11sUHhTzKaV21uX_FpBOEnJxNpFjUn6EHEiU79Pe3jXU",
        "sheetName": "VC_VENDAS",
        "valorAliases": ["VALOR A PAGAR"]
    }
}

CONFIG_TIPOS = {
    "VC_VENDAS": {"VENDAS": "ENTRADA"}
}

MESES_NOME = {
    1: "JANEIRO", 2: "FEVEREIRO", 3: "MARCO", 4: "ABRIL",
    5: "MAIO", 6: "JUNHO", 7: "JULHO", 8: "AGOSTO",
    9: "SETEMBRO", 10: "OUTUBRO", 11: "NOVEMBRO", 12: "DEZEMBRO"
}

MESES_NUM = {
    "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "MARÇO": 3, "ABRIL": 4,
    "MAIO": 5, "JUNHO": 6, "JULHO": 7, "AGOSTO": 8,
    "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12
}


# ============================================================================
# FUNÇÕES UTILITÁRIAS E NORMALIZAÇÃO (IDÊNTICAS À v57)
# ============================================================================

def txt57(valor: Any) -> str:
    return str(valor if valor is not None else "").strip()


def cmp57(valor: Any) -> str:
    t = txt57(valor).upper()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def desc57(valor: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", cmp57(valor))


def proc57(valor: Any) -> str:
    return re.sub(r"\s+", "", cmp57(valor))


def id57(valor: Any) -> str:
    return re.sub(r"\s+", "", cmp57(valor))


def doc57(valor: Any) -> str:
    if valor is None or valor == "":
        return ""
    if isinstance(valor, (int, float)) and math.isfinite(valor):
        return str(math.trunc(valor))
    t = txt57(valor)
    t = re.sub(r"\.0+$", "", t)
    return t.upper()


def num57(valor: Any) -> float:
    if isinstance(valor, (int, float)) and math.isfinite(valor):
        return float(valor)
    texto = txt57(valor).replace("€", "").replace(" ", "")
    if not texto:
        return 0.0
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    texto = re.sub(r"[^0-9.\-]", "", texto)
    try:
        n = float(texto)
        return n if math.isfinite(n) else 0.0
    except ValueError:
        return 0.0


def abs57(valor: Any) -> float:
    return abs(num57(valor))


def eq57(a: Any, b: Any, tolerancia: float = TOL) -> bool:
    return abs(num57(a) - num57(b)) <= tolerancia


def zero57(valor: Any) -> float:
    n = num57(valor)
    return 0.0 if abs(n) < 1e-9 else n


def fmt57(valor: Any) -> str:
    return f"{num57(valor):.2f}"


def data57(valor: Any) -> Optional[datetime]:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(valor, (int, float)) and math.isfinite(valor):
        try:
            d = datetime.fromtimestamp((valor - 25569) * 86400, tz=timezone.utc)
            return datetime(d.year, d.month, d.day)
        except Exception:
            return None
    t = txt57(valor)
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", t)
    if not m:
        return None
    dia, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime(ano, mes, dia)
    except ValueError:
        return None


def fmtData57(data: Optional[datetime]) -> str:
    return data.strftime("%d/%m/%Y") if data else ""


def chaveExclusao57(valor: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", cmp57(valor))


def descricaoExcluida57(desc: str, exclusoes: list[str]) -> bool:
    normalizada = cmp57(desc)
    compacta = chaveExclusao57(desc)
    if not normalizada and not compacta:
        return False
    for chave in exclusoes:
        chave_norm = cmp57(chave)
        chave_comp = chaveExclusao57(chave)
        if chave_norm and chave_norm in normalizada:
            return True
        if chave_comp and compacta and chave_comp in compacta:
            return True
    return False


def tipo57(valor: Any) -> str:
    tipo = cmp57(valor)
    if tipo in ["SAIDA", "PAGAMENTO", "DESPESA"]:
        return "SAÍDA"
    if tipo in ["ENTRADA", "RECEBIMENTO"]:
        return "ENTRADA"
    if tipo in ["TRANSFERENCIA", "TRANSFERIDO", "TRANSFER"]:
        return "TRANSFERÊNCIA"
    if tipo == "CARTAO":
        return "CARTÃO"
    if tipo == "MVV":
        return "MVV"
    if tipo in ["VENDAS", "VENDA"]:
        return "VENDAS"
    if tipo == "ESTORNO":
        return "ESTORNO"
    if tipo == "CREDITO":
        return "CRÉDITO"
    if tipo == "DEVOLUCAO":
        return "DEVOLUÇÃO"
    if tipo == "REEMBOLSO":
        return "REEMBOLSO"
    return tipo


def tipoFinanceiroSaidas57(tipo: Any) -> str:
    mapa = {
        "PAGAMENTO": "SAÍDA",
        "REEMBOLSO": "SAÍDA",
        "SAIDA": "SAÍDA",
        "DESPESA": "SAÍDA",
        "ESTORNO": "ENTRADA",
        "CREDITO": "ENTRADA",
        "DEVOLUCAO": "ENTRADA",
        "ENTRADA": "ENTRADA"
    }
    return mapa.get(cmp57(tipo), "")


def tipoFinanceiroGeral57(tipo: Any) -> str:
    t = cmp57(tipo)
    if t in ["ENTRADA", "RECEBIMENTO", "ESTORNO", "CREDITO", "DEVOLUCAO"]:
        return "ENTRADA"
    if t in ["SAIDA", "DESPESA", "PAGAMENTO", "REEMBOLSO"]:
        return "SAÍDA"
    return ""


def tipoFinanceiroExtrato57(item: Any) -> str:
    especial = tipo57(item.tipo)
    if especial in ["MVV", "CARTÃO", "TRANSFERÊNCIA"]:
        return especial
    semantico = tipoFinanceiroGeral57(item.tipo)
    if semantico:
        return semantico
    return "SAÍDA" if num57(item.valorRaw) < 0 else "ENTRADA"


def tipoOrigem57(procKey: str, origem: Any, configTipos: dict) -> str:
    if procKey in [proc57("SAÍDA"), proc57("SAÍDAS")]:
        return tipoFinanceiroSaidas57(origem.tipo)
    if procKey == proc57("DÍZIMOS/OFERTAS"):
        return "ENTRADA"
    if procKey == proc57("T_EXTRATO"):
        return tipoFinanceiroExtrato57(origem)
    for nome, regra in (configTipos or {}).items():
        if proc57(nome) != procKey:
            continue
        tipo = cmp57(origem.tipo)
        if tipo in regra:
            return tipoFinanceiroGeral57(regra[tipo]) or tipo57(regra[tipo])
    return tipoFinanceiroGeral57(origem.tipo) or tipo57(origem.tipo)


def valorFinanceiro57(tipo: Any, valor: Any) -> float:
    fin = tipoFinanceiroGeral57(tipo)
    esp = tipo57(tipo)
    v = abs57(valor)
    if fin == "ENTRADA":
        return v
    if fin == "SAÍDA":
        return -v
    if esp in ["CARTÃO", "MVV"]:
        return -v
    return num57(valor)


def processoCanonico57(valor: Any) -> str:
    key = proc57(valor)
    if key in [proc57("EXTRATO"), proc57("T_EXTRATO")]:
        return "T_EXTRATO"
    if key in [proc57("SAÍDA"), proc57("SAÍDAS")]:
        return "SAÍDAS"
    if key == proc57("DÍZIMOS/OFERTAS"):
        return "DÍZIMOS/OFERTAS"
    if key == proc57("FINANCEIRO"):
        return "FINANCEIRO"
    if key == proc57("VC_VENDAS"):
        return "VC_VENDAS"
    return txt57(valor)


def ehDocSomaReal57(valor: Any) -> bool:
    return bool(re.match(r"^\d+$", doc57(valor)))


def ehMVV57(item: Any) -> bool:
    return tipo57(item.tipo) == "MVV"


def ehDerivadoMVVSoma57(item: Any) -> bool:
    return cmp57(getattr(item, "desc", "")) in [
        "DIZIMOS 10%",
        "OFERTA COVV 2%",
        "OFERTA NOVAS OBRAS 2%",
        "OFERTA MISSOES 1%",
        "REPASSE LIVRARIA 3%"
    ]


def ehPgtoCartao57(item: Any) -> bool:
    return tipo57(item.tipo) == "CARTÃO" and cmp57(item.docSoma) in ["PGTO CARTAO", "PAGAMENTO CARTAO"]


def dispensaSomaAgregado57(item: Any) -> bool:
    return tipo57(item.tipo) in ["TRANSFERÊNCIA", "CARTÃO"]


# ============================================================================
# ESTRUTURAS DE DADOS
# ============================================================================

@dataclass
class ItemBase:
    linhaFonte: int = 0
    idInterno: str = ""
    docSoma: str = ""
    dataObj: Optional[datetime] = None
    dataFmt: str = ""
    dataValorObj: Optional[datetime] = None
    dataValorFmt: str = ""
    tipo: str = ""
    desc: str = ""
    descSoma: str = ""
    valorRaw: Any = 0.0
    processoProx: str = ""
    formaPagamento: str = ""
    imagemRecibo: str = ""


@dataclass
class Periodo:
    ano: int
    mes: int
    mesTexto: str
    dataInicial: datetime
    dataFinal: datetime


def noPeriodo57(item: Any, periodo: Periodo) -> bool:
    return bool(
        item and item.dataObj and periodo and
        periodo.dataInicial <= item.dataObj <= periodo.dataFinal
    )


@dataclass
class LinhaAudit:
    processo: str = ""
    idInterno: str = ""
    docSoma: str = ""
    data: str = ""
    tipo: str = ""
    desc: str = ""
    valor: Any = ""
    status: str = ""
    estilo: str = ""


def linhaAudit57(processo: str, item: Any, status: str = "", estilo: str = "") -> LinhaAudit:
    tipoRaw = getattr(item, "tipo", "")
    valorRaw = getattr(item, "valorRaw", "")
    return LinhaAudit(
        processo=processo,
        idInterno=getattr(item, "idInterno", ""),
        docSoma=getattr(item, "docSoma", ""),
        data=getattr(item, "dataFmt", ""),
        tipo=tipo57(tipoRaw) if tipoRaw != "" else "",
        desc=getattr(item, "desc", ""),
        valor="" if valorRaw == "" else valorFinanceiro57(tipoRaw, valorRaw),
        status=status,
        estilo=estilo
    )


def linhaVazia57(processo: str = "", id: str = "", doc: str = "", estilo: str = "") -> LinhaAudit:
    return LinhaAudit(processo=processo, idInterno=id, docSoma=doc, status="", estilo=estilo)


def linhaResumo57(processo: str, data: str, tipo: str, desc: str, valor: Any, status: str = "", estilo: str = "") -> LinhaAudit:
    return LinhaAudit(
        processo=processo,
        idInterno="",
        docSoma="",
        data=data or "",
        tipo=tipo or "",
        desc=desc or "",
        valor="" if valor == "" else num57(valor),
        status=status,
        estilo=estilo
    )


def linhaSeparador57() -> LinhaAudit:
    return linhaVazia57("", "", "", "separator")


# ============================================================================
# LEITURA E PARSING DE PLANILHAS
# ============================================================================

def colReq57(cols: dict[str, int], aliases: list[str]) -> int:
    for alias in aliases:
        k = cmp57(alias)
        if k in cols:
            return cols[k]
    raise ValueError(f"Coluna obrigatória não encontrada: {' | '.join(aliases)}")


def colOpt57(cols: dict[str, int], aliases: list[str]) -> int:
    for alias in aliases:
        k = cmp57(alias)
        if k in cols:
            return cols[k]
    return -1


def mapearColunas(header: list[str]) -> dict[str, int]:
    cols = {}
    for i, h in enumerate(header):
        k = cmp57(h)
        if k and k not in cols:
            cols[k] = i
    return cols


def parseCO57(rows: list[list[str]], cols: dict[str, int]) -> list[ItemBase]:
    iData = colReq57(cols, ["DATA MOV."])
    iDataValor = colOpt57(cols, ["DATA VALOR"])
    iDoc = colReq57(cols, ["DOC. SOMA"])
    iTipo = colReq57(cols, ["TIPO"])
    iDesc = colReq57(cols, ["DESCRIÇÃO", "DESCRICAO"])
    iDescSoma = colOpt57(cols, ["DESCRIÇÃO SOMA", "DESCRICAO SOMA"])
    iValor = colReq57(cols, ["IMPORTÂNCIA", "IMPORTANCIA"])
    iProc = colReq57(cols, ["PROCESSO"])
    iId = colReq57(cols, ["ID_INTERNO", "ID INTERNO"])
    iForma = colOpt57(cols, ["FORMA DE PAGAMENTO"])

    out = []
    for i, row in enumerate(rows):
        valData = row[iData] if iData < len(row) else ""
        dataObj = data57(valData)
        if not dataObj:
            continue
        valDataValor = row[iDataValor] if iDataValor >= 0 and iDataValor < len(row) else ""
        dataValorObj = data57(valDataValor) if iDataValor >= 0 else None

        out.append(ItemBase(
            linhaFonte=i + 2,
            idInterno=row[iId] if iId < len(row) else "",
            docSoma=row[iDoc] if iDoc < len(row) else "",
            dataObj=dataObj,
            dataFmt=fmtData57(dataObj),
            dataValorObj=dataValorObj,
            dataValorFmt=fmtData57(dataValorObj) if dataValorObj else "",
            tipo=row[iTipo] if iTipo < len(row) else "",
            desc=row[iDesc] if iDesc < len(row) else "",
            descSoma=row[iDescSoma] if iDescSoma >= 0 and iDescSoma < len(row) else "",
            valorRaw=row[iValor] if iValor < len(row) else "",
            processoProx=row[iProc] if iProc < len(row) else "",
            formaPagamento=row[iForma] if iForma >= 0 and iForma < len(row) else ""
        ))
    return out


def parseSoma57(rows: list[list[str]], cols: dict[str, int]) -> list[ItemBase]:
    iDoc = colReq57(cols, ["CODIGO", "CÓDIGO"])
    iTipo = colReq57(cols, ["TIPO"])
    iDesc = colReq57(cols, ["DESCRIÇÃO", "DESCRICAO"])
    iValor = colReq57(cols, ["VALOR"])
    iData = colReq57(cols, ["PAGAMENTO"])

    out = []
    for i, row in enumerate(rows):
        valData = row[iData] if iData < len(row) else ""
        dataObj = data57(valData)
        out.append(ItemBase(
            linhaFonte=i + 2,
            idInterno="",
            docSoma=row[iDoc] if iDoc < len(row) else "",
            dataObj=dataObj,
            dataFmt=fmtData57(dataObj),
            tipo=row[iTipo] if iTipo < len(row) else "",
            desc=row[iDesc] if iDesc < len(row) else "",
            valorRaw=row[iValor] if iValor < len(row) else "",
            formaPagamento=""
        ))
    return out


def parseSaidas57(rows: list[list[str]], cols: dict[str, int]) -> list[ItemBase]:
    iId = colReq57(cols, ["ID_INTERNO", "ID INTERNO"])
    iForma = colReq57(cols, ["FORMA DE PAGAMENTO"])
    iData = colReq57(cols, ["DATA"])
    iDataValor = colOpt57(cols, ["DATA VALOR"])
    iTipo = colOpt57(cols, ["TIPO"])
    iDoc = colReq57(cols, ["DOC. SOMA"])
    iValor = colReq57(cols, ["VALOR DA COMPRA"])
    iImagem = colOpt57(cols, ["IMAGEM DO RECIBO"])
    iDesc = colReq57(cols, ["DESCRIÇÃO DA COMPRA", "DESCRICAO DA COMPRA"])

    out = []
    for i, row in enumerate(rows):
        valData = row[iData] if iData < len(row) else ""
        dataObj = data57(valData)
        valDataValor = row[iDataValor] if iDataValor >= 0 and iDataValor < len(row) else ""
        dataValorObj = data57(valDataValor) if iDataValor >= 0 else None

        out.append(ItemBase(
            linhaFonte=i + 2,
            idInterno=row[iId] if iId < len(row) else "",
            docSoma=row[iDoc] if iDoc < len(row) else "",
            dataObj=dataObj,
            dataFmt=fmtData57(dataObj),
            dataValorObj=dataValorObj,
            dataValorFmt=fmtData57(dataValorObj) if dataValorObj else "",
            tipo=row[iTipo] if iTipo >= 0 and iTipo < len(row) else "",
            desc=row[iDesc] if iDesc < len(row) else "",
            descSoma="",
            valorRaw=row[iValor] if iValor < len(row) else "",
            formaPagamento=row[iForma] if iForma < len(row) else "",
            imagemRecibo=row[iImagem] if iImagem >= 0 and iImagem < len(row) else ""
        ))
    return out


def parseOrigemGenerica57(rows: list[list[str]], cols: dict[str, int], procKey: str, valorAliases: list[str]) -> list[ItemBase]:
    if procKey in [proc57("SAÍDA"), proc57("SAÍDAS")]:
        return parseSaidas57(rows, cols)
    iId = colReq57(cols, ["ID_INTERNO", "ID INTERNO"])
    iDoc = colReq57(cols, ["DOC. SOMA"])
    iData = colReq57(cols, ["DATA MOV.", "DATA", "PAGAMENTO"])
    iTipo = colOpt57(cols, ["TIPO"])
    iValor = colReq57(cols, valorAliases)
    iForma = colOpt57(cols, ["FORMA DE PAGAMENTO"])
    descAliases = (
        ["NÚMERO DOCUMENTO", "NUMERO DOCUMENTO", "DESCRIÇÃO", "DESCRICAO"]
        if procKey == proc57("DÍZIMOS/OFERTAS")
        else ["DESCRIÇÃO", "DESCRICAO"]
    )
    iDesc = colOpt57(cols, descAliases)

    out = []
    for i, row in enumerate(rows):
        valData = row[iData] if iData < len(row) else ""
        dataObj = data57(valData)
        out.append(ItemBase(
            linhaFonte=i + 2,
            idInterno=row[iId] if iId < len(row) else "",
            docSoma=row[iDoc] if iDoc < len(row) else "",
            dataObj=dataObj,
            dataFmt=fmtData57(dataObj),
            tipo=row[iTipo] if iTipo >= 0 and iTipo < len(row) else "",
            desc=row[iDesc] if iDesc >= 0 and iDesc < len(row) else "",
            valorRaw=row[iValor] if iValor < len(row) else "",
            formaPagamento=row[iForma] if iForma >= 0 and iForma < len(row) else ""
        ))
    return out


# ============================================================================
# CARREGADOR DE PLANILHAS COM CACHE (ALTA PERFORMANCE)
# ============================================================================

class SheetsDataManager:
    """Carrega todas as sheets necessárias uma única vez na memória."""

    def __init__(self, gc: gspread.Client, main_url: str):
        self.gc = gc
        self.main_url = main_url
        self.sh_main = gc.open_by_url(main_url)
        self.sh_ext = gc.open_by_key(CONFIG_EXT["FINANCEIRO"]["spreadsheetId"])
        
        self.raw_cache: dict[str, list[list[str]]] = {}
        self.parsed_cache: dict[str, list[ItemBase]] = {}
        self.exclusoes_originais: list[str] = []
        self.exclusoes_normalizadas: list[str] = []

    def _get_ws(self, sh, name: str):
        try:
            return sh.worksheet(name)
        except Exception:
            name_cmp = cmp57(name)
            for ws in sh.worksheets():
                if cmp57(ws.title) == name_cmp:
                    return ws
            raise ValueError(f"Sheet '{name}' não encontrada no spreadsheet.")

    def carregar_tudo(self):
        logger.info("Carregando planilhas do Google Sheets para cache em memória...")
        
        # 1. T_AUDIT (para exclusões e período inicial)
        ws_audit = self._get_ws(self.sh_main, "T_AUDIT")
        vals_audit = ws_audit.get_all_values()
        self.raw_cache["T_AUDIT"] = vals_audit
        self._extrair_exclusoes(vals_audit)

        # 2. CONTAORDEM
        ws_co = self._get_ws(self.sh_main, "CONTAORDEM")
        vals_co = ws_co.get_all_values()
        cols_co = mapearColunas(vals_co[0]) if vals_co else {}
        self.parsed_cache["CONTAORDEM_RAW"] = parseCO57(vals_co[1:], cols_co)

        # 3. SOMA
        ws_soma = self._get_ws(self.sh_main, "SOMA")
        vals_soma = ws_soma.get_all_values()
        cols_soma = mapearColunas(vals_soma[0]) if vals_soma else {}
        self.parsed_cache["SOMA"] = parseSoma57(vals_soma[1:], cols_soma)

        # 4. SAÍDAS
        ws_saidas = self._get_ws(self.sh_main, "SAÍDAS")
        vals_saidas = ws_saidas.get_all_values()
        cols_saidas = mapearColunas(vals_saidas[0]) if vals_saidas else {}
        self.parsed_cache["SAÍDAS"] = parseSaidas57(vals_saidas[1:], cols_saidas)

        # 5. T_EXTRATO
        ws_extrato = self._get_ws(self.sh_main, "T_EXTRATO")
        vals_extrato = ws_extrato.get_all_values()
        cols_extrato = mapearColunas(vals_extrato[0]) if vals_extrato else {}
        self.parsed_cache["T_EXTRATO"] = parseOrigemGenerica57(
            vals_extrato[1:], cols_extrato, proc57("T_EXTRATO"), ["IMPORTÂNCIA", "IMPORTANCIA", "VALOR"]
        )

        # 6. DÍZIMOS/OFERTAS
        ws_dizimos = self._get_ws(self.sh_main, "DÍZIMOS/OFERTAS")
        vals_dizimos = ws_dizimos.get_all_values()
        cols_dizimos = mapearColunas(vals_dizimos[0]) if vals_dizimos else {}
        self.parsed_cache["DÍZIMOS/OFERTAS"] = parseOrigemGenerica57(
            vals_dizimos[1:], cols_dizimos, proc57("DÍZIMOS/OFERTAS"), ["IMPORTÂNCIA", "IMPORTANCIA", "VALOR"]
        )

        # 7. Externo: Financeiro
        ws_fin = self._get_ws(self.sh_ext, CONFIG_EXT["FINANCEIRO"]["sheetName"])
        vals_fin = ws_fin.get_all_values()
        cols_fin = mapearColunas(vals_fin[0]) if vals_fin else {}
        self.parsed_cache["FINANCEIRO"] = parseOrigemGenerica57(
            vals_fin[1:], cols_fin, proc57("FINANCEIRO"), CONFIG_EXT["FINANCEIRO"]["valorAliases"]
        )

        # 8. Externo: VC_VENDAS
        ws_vendas = self._get_ws(self.sh_ext, CONFIG_EXT["VC_VENDAS"]["sheetName"])
        vals_vendas = ws_vendas.get_all_values()
        cols_vendas = mapearColunas(vals_vendas[0]) if vals_vendas else {}
        self.parsed_cache["VC_VENDAS"] = parseOrigemGenerica57(
            vals_vendas[1:], cols_vendas, proc57("VC_VENDAS"), CONFIG_EXT["VC_VENDAS"]["valorAliases"]
        )

        logger.info(
            f"Carregamento concluído: CO={len(self.parsed_cache['CONTAORDEM_RAW'])}, "
            f"SOMA={len(self.parsed_cache['SOMA'])}, SAÍDAS={len(self.parsed_cache['SAÍDAS'])}, "
            f"EXTRATO={len(self.parsed_cache['T_EXTRATO'])}, DÍZIMOS={len(self.parsed_cache['DÍZIMOS/OFERTAS'])}, "
            f"FINANCEIRO={len(self.parsed_cache['FINANCEIRO'])}, VC_VENDAS={len(self.parsed_cache['VC_VENDAS'])}"
        )

    def _extrair_exclusoes(self, dados: list[list[str]]):
        alvo = cmp57("EXCLUIR DO PROCESSAMENTO")
        inicio = -1
        for i, row in enumerate(dados):
            c0 = cmp57(row[0]) if len(row) > 0 else ""
            c1 = cmp57(row[1]) if len(row) > 1 else ""
            if c0 == alvo or c1 == alvo:
                inicio = i
                break
        if inicio < 0:
            self.exclusoes_originais = []
            self.exclusoes_normalizadas = []
            return

        originais = []
        normalizadas = []
        for i in range(inicio + 1, len(dados)):
            row = dados[i]
            a = txt57(row[0]) if len(row) > 0 else ""
            b = txt57(row[1]) if len(row) > 1 else ""
            if not a and not b:
                if originais:
                    break
                continue
            val = b or a
            if val:
                originais.append(val)
                normalizadas.append(cmp57(val))
        self.exclusoes_originais = originais
        self.exclusoes_normalizadas = normalizadas
        logger.info(f"Exclusões identificadas ({len(originais)}): {originais}")

    def obter_periodo_planilha(self) -> Periodo:
        vals = self.raw_cache.get("T_AUDIT", [])
        if not vals or len(vals) < 1:
            raise ValueError("T_AUDIT está vazio.")
        ano_str = vals[0][0] if len(vals[0]) > 0 else ""
        mes_str = vals[0][1] if len(vals[0]) > 1 else ""
        ano = int(ano_str) if ano_str.isdigit() else 2026
        mes_cmp = cmp57(mes_str)
        mes = MESES_NUM.get(mes_cmp, 9)
        return criar_periodo(ano, mes)


def criar_periodo(ano: int, mes: int) -> Periodo:
    _, last_day = calendar.monthrange(ano, mes)
    data_inicial = datetime(ano, mes, 1, 0, 0, 0)
    data_final = datetime(ano, mes, last_day, 0, 0, 0)
    return Periodo(
        ano=ano,
        mes=mes,
        mesTexto=MESES_NOME[mes],
        dataInicial=data_inicial,
        dataFinal=data_final
    )


# ============================================================================
# ÍNDICES E AUXILIARES
# ============================================================================

def idxDoc57(itens: list[ItemBase]) -> dict[str, list[ItemBase]]:
    m = defaultdict(list)
    for it in itens:
        k = doc57(it.docSoma)
        if k:
            m[k].append(it)
    return m


def idxId57(itens: list[ItemBase]) -> dict[str, list[ItemBase]]:
    m = defaultdict(list)
    for it in itens:
        k = id57(it.idInterno)
        if k:
            m[k].append(it)
    return m


def idxCOProcId57(itens: list[ItemBase]) -> dict[str, list[ItemBase]]:
    m = defaultdict(list)
    for it in itens:
        p = proc57(it.processoProx)
        i = id57(it.idInterno)
        if p and i:
            m[f"{p}|{i}"].append(it)
    return m


def matchId57(indice: Optional[dict[str, list[ItemBase]]], id_val: Any) -> dict:
    k = id57(id_val)
    if not k or not indice:
        return {"item": None, "duplicado": False}
    lista = indice.get(k, [])
    return {"item": lista[0] if lista else None, "duplicado": len(lista) > 1}


def matchCOProcId57(indice: Optional[dict[str, list[ItemBase]]], processos: list[str], id_val: Any) -> dict:
    idKey = id57(id_val)
    if not idKey or not indice:
        return {"item": None, "duplicado": False}
    lista = []
    for proc in processos:
        k = f"{proc57(proc)}|{idKey}"
        lista.extend(indice.get(k, []))
    return {"item": lista[0] if lista else None, "duplicado": len(lista) > 1}


# ============================================================================
# DOC SOMA CARDINALIDADE E SEQUENCIAL N001..N999
# ============================================================================

def baseDescricaoSoma57(texto: str) -> str:
    t = txt57(texto)
    if not t:
        return ""
    t = re.sub(r"\s+N\d{1,3}$", "", t, flags=re.I)
    t = re.sub(r"\s+N[0O]{0,2}\d{1,3}$", "", t, flags=re.I)
    return t.strip()


def validarSequencialDescricao57(coAll: list[ItemBase]) -> dict[int, list[str]]:
    gruposFallback = defaultdict(list)
    problemas = defaultdict(list)

    for item in coAll:
        texto = txt57(item.descSoma)
        if not texto or not item.dataFmt:
            continue
        # CORREÇÃO v57: Se existe DOC.SOMA numérico real, SOMA.DESCRIÇÃO é a fonte de verdade!
        if ehDocSomaReal57(item.docSoma):
            continue

        base = baseDescricaoSoma57(texto)
        chave = f"{item.dataFmt}|{cmp57(base)}"
        gruposFallback[chave].append(item)

    for lista in gruposFallback.values():
        lista.sort(key=lambda x: x.linhaFonte)
        for i, item in enumerate(lista):
            esperado = i + 1
            texto = txt57(item.descSoma)
            m = re.search(r"\s+N(\d{3})$", texto, re.I)
            erros = []
            if not m:
                erros.append("DESCRICAO_SOMA_SEM_SEQUENCIAL")
            else:
                atual = int(m.group(1))
                if atual < 1 or atual > 999:
                    erros.append("DESCRICAO_SOMA_SEQUENCIAL_FORA_INTERVALO")
                if atual != esperado:
                    erros.append(
                        f"DESCRICAO_SOMA_SEQUENCIAL_DIVERGE(ATUAL=N{atual:03d}|ESPERADO=N{esperado:03d})"
                    )
            if esperado > 999:
                erros.append("DESCRICAO_SOMA_GRUPO_ACIMA_N999")
            if erros:
                problemas[item.linhaFonte].extend(erros)

    return problemas


def matchSomaParaCO57(co: ItemBase, ctx: ContextoAuditoria) -> dict:
    doc = doc57(co.docSoma)
    if not ehDocSomaReal57(doc):
        return {
            "item": None, "duplicado": False, "usoCO": 0, "usoSoma": 0,
            "cardinalidadeInsuficiente": False, "ambiguo": False
        }
    listaSoma = ctx.idxSoma.get(doc, [])
    listaCO = ctx.idxCODoc.get(doc, [])
    if not listaSoma:
        return {
            "item": None, "duplicado": False, "usoCO": len(listaCO), "usoSoma": 0,
            "cardinalidadeInsuficiente": len(listaCO) > 0, "ambiguo": False
        }

    avaliados = []
    for item in listaSoma:
        score = 0
        if eq57(abs57(item.valorRaw), abs57(co.valorRaw), ctx.tol):
            score += 10
        tipoCO = tipoFinanceiroGeral57(co.tipo)
        tipoSoma = tipoFinanceiroGeral57(item.tipo)
        if tipoCO and tipoSoma and tipoCO == tipoSoma:
            score += 5
        if txt57(co.descSoma) and txt57(item.desc) and cmp57(co.descSoma) == cmp57(item.desc):
            score += 5
        if co.dataFmt and item.dataFmt and co.dataFmt == item.dataFmt:
            score += 1
        avaliados.append((score, item))

    avaliados.sort(key=lambda x: x[0], reverse=True)
    top_score, top_item = avaliados[0]
    ambiguo = len(avaliados) > 1 and avaliados[1][0] == top_score

    return {
        "item": top_item,
        "duplicado": len(listaSoma) > 1,
        "usoCO": len(listaCO),
        "usoSoma": len(listaSoma),
        "cardinalidadeInsuficiente": len(listaCO) > len(listaSoma),
        "ambiguo": ambiguo
    }


def docsReutilizadosPeriodo57(coPeriodo: list[ItemBase], ctx: ContextoAuditoria) -> list[dict]:
    vistos = set()
    out = []
    for item in coPeriodo or []:
        doc = doc57(item.docSoma)
        if not ehDocSomaReal57(doc) or doc in vistos:
            continue
        vistos.add(doc)
        qtdCO = len(ctx.idxCODoc.get(doc, []))
        qtdSoma = len(ctx.idxSoma.get(doc, []))
        if qtdCO > qtdSoma:
            out.append({"doc": doc, "qtdCO": qtdCO, "qtdSoma": qtdSoma})
    return out


def somaSemContaOrdemPeriodo57(ctx: ContextoAuditoria) -> list[ItemBase]:
    out = []
    for item in ctx.soma or []:
        if not noPeriodo57(item, ctx.periodo):
            continue
        doc = doc57(item.docSoma)
        if not ehDocSomaReal57(doc):
            continue
        if descricaoExcluida57(item.desc, ctx.exclusoes):
            continue
        if ehDerivadoMVVSoma57(item):
            continue
        correspondentes = ctx.idxCODoc.get(doc, [])
        if not correspondentes:
            out.append(item)
    return out


# ============================================================================
# COMPARAÇÕES DE ITENS
# ============================================================================

def compararCOOrigem57(co: ItemBase, origem: ItemBase, procKey: str, ctx: ContextoAuditoria) -> list[str]:
    divergencias = []
    if not eq57(abs57(co.valorRaw), abs57(origem.valorRaw), ctx.tol):
        divergencias.append("VALOR_ORIGEM_DIVERGE")
    if co.dataFmt and origem.dataFmt and co.dataFmt != origem.dataFmt:
        divergencias.append("DATA_ORIGEM_DIVERGE")

    tipoCO = tipoFinanceiroGeral57(co.tipo) or tipo57(co.tipo)
    tipoOrigem = tipoOrigem57(procKey, origem, ctx.CONFIG_TIPOS)
    if procKey in [proc57("SAÍDA"), proc57("SAÍDAS")] and not tipoOrigem:
        divergencias.append(f"TIPO_SAIDAS_NAO_MAPEADO={origem.tipo or '?'}")
    elif tipoCO and tipoOrigem and tipoCO != tipoOrigem:
        divergencias.append(
            f"TIPO_ORIGEM_DIVERGE(CO={tipoCO}|ORIGEM={tipo57(origem.tipo)}|EQUIVALENTE={tipoOrigem})"
        )

    processosSemCompararDesc = [
        proc57("DÍZIMOS/OFERTAS"),
        proc57("VC_VENDAS"),
        proc57("FINANCEIRO")
    ]
    if procKey not in processosSemCompararDesc:
        d_co = desc57(co.desc)
        d_orig = desc57(origem.desc)
        if d_co != d_orig:
            d_co_limpo = re.sub(r"^TRFP?", "TRF", re.sub(r"IPSP", "", d_co))
            d_orig_limpo = re.sub(r"^TRFP?", "TRF", re.sub(r"IPSP", "", d_orig))
            min_len = min(len(d_co_limpo), len(d_orig_limpo))
            if min_len >= 10 and d_co_limpo[:min_len] == d_orig_limpo[:min_len]:
                pass
            else:
                divergencias.append("DESCRICAO_ORIGEM_DIVERGE")

    if doc57(co.docSoma) != doc57(origem.docSoma):
        divergencias.append("DOC_ORIGEM_DIVERGE")

    return divergencias


def compararCOSoma57(co: ItemBase, soma: ItemBase, tol: float) -> list[str]:
    divergencias = []
    if not eq57(abs57(co.valorRaw), abs57(soma.valorRaw), tol):
        divergencias.append("VALOR_SOMA_DIVERGE")
    if doc57(co.docSoma) != doc57(soma.docSoma):
        divergencias.append("DOC_SOMA_DIVERGE")
    if co.dataFmt and soma.dataFmt and co.dataFmt != soma.dataFmt:
        divergencias.append("DATA_SOMA_DIVERGE")
    tipoCO = tipoFinanceiroGeral57(co.tipo)
    tipoSoma = tipoFinanceiroGeral57(soma.tipo)
    if tipoCO and tipoSoma and tipoCO != tipoSoma:
        divergencias.append("TIPO_SOMA_DIVERGE")
    if txt57(co.descSoma) and txt57(soma.desc) and cmp57(co.descSoma) != cmp57(soma.desc):
        divergencias.append("DESCRICAO_SOMA_DIVERGE")
    return divergencias


# ============================================================================
# CARTÃO - CICLOS E HISTÓRICO
# ============================================================================

def cicloPagamentoCartao57(item: ItemBase) -> str:
    return chaveMes57(item.dataObj) if item and item.dataObj else ""


def cicloDetalheCartao57(item: ItemBase) -> str:
    if item and item.dataObj:
        return chaveMes57(item.dataObj)
    imagem = txt57(getattr(item, "imagemRecibo", ""))
    m = re.search(r"(\d{2})-(\d{4})", imagem)
    if m:
        mes = int(m.group(1))
        ano = int(m.group(2))
        if 1 <= mes <= 12 and ano >= 2000:
            return f"{ano:04d}-{mes:02d}"
    return ""


def chaveMes57(data: datetime) -> str:
    return f"{data.year:04d}-{data.month:02d}"


def rotuloMes57(chave: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})$", str(chave or ""))
    return f"{m.group(2)}/{m.group(1)}" if m else (chave or "")


def ehCicloCartaoHistorico57(chave: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}$", str(chave or ""))) and str(chave) < "2026-04"


def localizarDetalhesCartaoHistoricoSoma57(ciclo: Any, soma: list[ItemBase], tol: float) -> dict:
    datas = {x.dataFmt for x in ciclo.pagamentos if x.dataFmt}
    alvoCent = round(abs57(ciclo.pagamentosTotal) * 100)

    candidatos = []
    for x in soma or []:
        if x.dataFmt not in datas:
            continue
        if tipoFinanceiroGeral57(x.tipo) != "SAÍDA":
            continue
        if ehDerivadoMVVSoma57(x):
            continue
        d = cmp57(x.desc)
        if d.startswith("DESPESAS BANCARIAS") or d.startswith("IMPOSTO SOBRE OPERACOES FINANCEIRAS"):
            continue
        cent = round(abs57(x.valorRaw) * 100)
        if 0 < cent <= alvoCent:
            candidatos.append(x)

    estados = {0: [[]]}
    for idx, item in enumerate(candidatos):
        valor = round(abs57(item.valorRaw) * 100)
        snapshot = sorted(estados.items(), key=lambda x: x[0], reverse=True)
        for somaAtual, solucoes in snapshot:
            nova = somaAtual + valor
            if nova > alvoCent:
                continue
            existentes = estados.setdefault(nova, [])
            for sol in solucoes:
                if len(existentes) < 2:
                    existentes.append(sol + [idx])

    solucoes = estados.get(alvoCent, [])
    indices = solucoes[0] if solucoes else []
    return {
        "itens": [candidatos[i] for i in indices],
        "encontrado": len(solucoes) > 0,
        "ambiguo": len(solucoes) > 1,
        "candidatos": len(candidatos)
    }


@dataclass
class CicloCartao:
    chave: str = ""
    rotulo: str = ""
    pagamentos: list[ItemBase] = field(default_factory=list)
    componentes: list[ItemBase] = field(default_factory=list)
    componentesSomaHistorico: list[ItemBase] = field(default_factory=list)
    pagamentosTotal: float = 0.0
    despesasBrutas: float = 0.0
    creditos: float = 0.0
    saldoInicial: float = 0.0
    saldoFinal: float = 0.0
    fechoDireto: bool = False
    ledgerConfiavel: bool = False
    ancora: str = ""
    dataReferenciaFmt: str = ""
    temProximo: bool = False
    modoHistorico: bool = False
    historicoEncontrado: bool = False
    historicoAmbiguo: bool = False


def construirCiclosCartao57(coAll: list[ItemBase], saidas: list[ItemBase], soma: list[ItemBase], tol: float) -> dict[str, CicloCartao]:
    mapping: dict[str, CicloCartao] = {}

    def obter(chave: str) -> CicloCartao:
        if chave not in mapping:
            mapping[chave] = CicloCartao(chave=chave, rotulo=rotuloMes57(chave))
        return mapping[chave]

    for item in coAll or []:
        if not ehPgtoCartao57(item):
            continue
        chave = cicloPagamentoCartao57(item)
        if not chave:
            continue
        ciclo = obter(chave)
        ciclo.pagamentos.append(item)
        ciclo.pagamentosTotal += abs57(item.valorRaw)
        if not ciclo.dataReferenciaFmt:
            ciclo.dataReferenciaFmt = item.dataFmt

    for item in saidas or []:
        if cmp57(item.formaPagamento) != "CARTAO DE CREDITO":
            continue
        chave = cicloDetalheCartao57(item)
        if not chave or ehCicloCartaoHistorico57(chave):
            continue
        ciclo = obter(chave)
        ciclo.componentes.append(item)
        fin = tipoFinanceiroSaidas57(item.tipo)
        if fin == "SAÍDA":
            ciclo.despesasBrutas += abs57(item.valorRaw)
        elif fin == "ENTRADA":
            ciclo.creditos += abs57(item.valorRaw)

    for ciclo in mapping.values():
        ciclo.modoHistorico = ehCicloCartaoHistorico57(ciclo.chave)
        if not ciclo.modoHistorico or not ciclo.pagamentos:
            continue
        hist = localizarDetalhesCartaoHistoricoSoma57(ciclo, soma, tol)
        ciclo.componentesSomaHistorico = hist["itens"]
        ciclo.historicoAmbiguo = hist["ambiguo"]
        ciclo.historicoEncontrado = hist["encontrado"]
        ciclo.despesasBrutas = sum(abs57(x.valorRaw) for x in hist["itens"])
        ciclo.creditos = 0.0

    chaves = sorted(mapping.keys())
    saldo = 0.0
    ancoraAtiva = False

    for i, chave in enumerate(chaves):
        ciclo = mapping[chave]
        ciclo.fechoDireto = eq57(
            ciclo.pagamentosTotal + ciclo.creditos,
            ciclo.despesasBrutas,
            tol
        ) and (
            ciclo.pagamentosTotal > 0 or
            ciclo.despesasBrutas > 0 or
            ciclo.creditos > 0
        )

        if ciclo.fechoDireto:
            ciclo.saldoInicial = 0.0
            ciclo.saldoFinal = 0.0
            ciclo.ledgerConfiavel = True
            ciclo.ancora = "FECHO_DIRETO"
            saldo = 0.0
            ancoraAtiva = True
        else:
            ciclo.saldoInicial = zero57(saldo)
            ciclo.saldoFinal = zero57(
                ciclo.saldoInicial +
                ciclo.pagamentosTotal +
                ciclo.creditos -
                ciclo.despesasBrutas
            )
            ciclo.ledgerConfiavel = ancoraAtiva
            ciclo.ancora = "CARRY_DE_ANCORA_ANTERIOR" if ancoraAtiva else "SEM_ANCORA_ANTERIOR"
            saldo = ciclo.saldoFinal

        ciclo.temProximo = (i < len(chaves) - 1)
        if not ciclo.dataReferenciaFmt:
            ano, mes = map(int, chave.split("-"))
            ciclo.dataReferenciaFmt = fmtData57(datetime(ano, mes, 1))

    return mapping


# ============================================================================
# COBERTURA DE ORIGENS
# ============================================================================

def origemEsperadaNoCO57(processo: str, item: ItemBase) -> bool:
    if abs57(item.valorRaw) <= 0:
        return False
    if processo == "VC_VENDAS":
        return cmp57(item.formaPagamento) == "DINHEIRO"
    return True


@dataclass
class ResultadoProcessoCobertura:
    processo: str
    qtdCO: int
    qtdOrigem: int
    faltantes: list[ItemBase] = field(default_factory=list)
    extras: list[ItemBase] = field(default_factory=list)
    duplicadosCO: list[dict] = field(default_factory=list)
    duplicadosOrigem: list[dict] = field(default_factory=list)
    semIdCO: list[ItemBase] = field(default_factory=list)
    semIdOrigem: list[ItemBase] = field(default_factory=list)
    erro: str = ""


@dataclass
class CoberturaGeral:
    processos: list[ResultadoProcessoCobertura] = field(default_factory=list)
    totalFalhas: int = 0


def validarCoberturaOrigens57(coPeriodoRaw: list[ItemBase], ctx: ContextoAuditoria) -> CoberturaGeral:
    processos = ["T_EXTRATO", "SAÍDAS", "DÍZIMOS/OFERTAS", "FINANCEIRO", "VC_VENDAS"]
    resultados = []
    totalFalhas = 0

    for processo in processos:
        origem = ctx.origem(processo)
        coProcRaw = [x for x in coPeriodoRaw if processoCanonico57(x.processoProx) == processo]

        if not origem or origem.get("erro"):
            coProc = [x for x in coProcRaw if not descricaoExcluida57(x.desc, ctx.exclusoes)]
            erro = origem.get("erro", "ORIGEM_NAO_LOCALIZADA") if origem else "ORIGEM_NAO_LOCALIZADA"
            r = ResultadoProcessoCobertura(
                processo=processo,
                qtdCO=len(coProc),
                qtdOrigem=0,
                semIdCO=[x for x in coProc if not id57(x.idInterno)],
                erro=erro
            )
            resultados.append(r)
            totalFalhas += 1
            continue

        origemPeriodoRaw = [
            x for x in origem["itens"]
            if noPeriodo57(x, ctx.periodo) and origemEsperadaNoCO57(processo, x)
        ]

        # CORREÇÃO v57: EXCLUSÃO SIMÉTRICA POR ID_INTERNO
        idsExcluidos = set()
        for it in coProcRaw:
            if descricaoExcluida57(it.desc, ctx.exclusoes):
                i = id57(it.idInterno)
                if i:
                    idsExcluidos.add(i)

        for it in origemPeriodoRaw:
            if descricaoExcluida57(it.desc, ctx.exclusoes):
                i = id57(it.idInterno)
                if i:
                    idsExcluidos.add(i)

        def manter(item: ItemBase) -> bool:
            i = id57(item.idInterno)
            if i and i in idsExcluidos:
                return False
            if not i and descricaoExcluida57(item.desc, ctx.exclusoes):
                return False
            return True

        coProc = [x for x in coProcRaw if manter(x)]
        origemPeriodo = [x for x in origemPeriodoRaw if manter(x)]

        mapCO = idxId57(coProc)
        mapOrig = idxId57(origemPeriodo)

        semIdCO = [x for x in coProc if not id57(x.idInterno)]
        semIdOrigem = [x for x in origemPeriodo if not id57(x.idInterno)]

        faltantes = []
        for i_val, lista in mapOrig.items():
            if i_val not in mapCO:
                faltantes.extend(lista)

        extras = []
        for i_val, lista in mapCO.items():
            if i_val not in mapOrig:
                extras.extend(lista)

        duplicadosCO = [
            {"id": i_val, "quantidade": len(lista), "itens": lista}
            for i_val, lista in mapCO.items() if len(lista) > 1
        ]
        duplicadosOrigem = [
            {"id": i_val, "quantidade": len(lista), "itens": lista}
            for i_val, lista in mapOrig.items() if len(lista) > 1
        ]

        r = ResultadoProcessoCobertura(
            processo=processo,
            qtdCO=len(coProc),
            qtdOrigem=len(origemPeriodo),
            faltantes=faltantes,
            extras=extras,
            duplicadosCO=duplicadosCO,
            duplicadosOrigem=duplicadosOrigem,
            semIdCO=semIdCO,
            semIdOrigem=semIdOrigem,
            erro=""
        )
        totalFalhas += (
            len(faltantes) + len(extras) + len(duplicadosCO) +
            len(duplicadosOrigem) + len(semIdCO) + len(semIdOrigem)
        )
        resultados.append(r)

    return CoberturaGeral(processos=resultados, totalFalhas=totalFalhas)


def linhasCobertura57(cobertura: CoberturaGeral) -> list[LinhaAudit]:
    linhas = []
    for c in cobertura.processos or []:
        falhas = (
            len(c.faltantes) + len(c.extras) + len(c.duplicadosCO) +
            len(c.duplicadosOrigem) + len(c.semIdCO) + len(c.semIdOrigem) +
            (1 if c.erro else 0)
        )
        linhas.append(LinhaAudit(
            processo=f"COBERTURA — {c.processo}",
            tipo="CONTROLE",
            desc=(
                f"CONTAORDEM={c.qtdCO} | ORIGEM={c.qtdOrigem} | "
                f"FALTANTES={len(c.faltantes)} | EXTRAS={len(c.extras)} | "
                f"DUP.CO={len(c.duplicadosCO)} | DUP.ORIG={len(c.duplicadosOrigem)}"
            ),
            status="❌" if falhas else "✅",
            estilo="coverageError" if falhas else "coverageSummary"
        ))

        if c.erro:
            linhas.append(LinhaAudit(
                processo=f"↳↳ ERRO ORIGEM — {c.processo}",
                tipo="CONTROLE",
                desc=c.erro,
                status="❌",
                estilo="coverageError"
            ))

        for it in c.faltantes:
            linhas.append(linhaAudit57(f"↳↳ ORIGEM SEM CONTAORDEM — {c.processo}", it, "❌", "coverageError"))
        for it in c.extras:
            linhas.append(linhaAudit57(f"↳↳ CONTAORDEM SEM ORIGEM — {c.processo}", it, "❌", "coverageError"))
        for dup in c.duplicadosCO:
            linhas.append(LinhaAudit(
                processo=f"↳↳ ID DUPLICADO CONTAORDEM — {c.processo}",
                idInterno=dup["id"],
                tipo="CONTROLE",
                desc=f"QUANTIDADE={dup['quantidade']}",
                status="❌",
                estilo="coverageError"
            ))
        for dup in c.duplicadosOrigem:
            linhas.append(LinhaAudit(
                processo=f"↳↳ ID DUPLICADO ORIGEM — {c.processo}",
                idInterno=dup["id"],
                tipo="CONTROLE",
                desc=f"QUANTIDADE={dup['quantidade']}",
                status="❌",
                estilo="coverageError"
            ))
        for it in c.semIdCO:
            linhas.append(linhaAudit57(f"↳↳ CONTAORDEM SEM ID_INTERNO — {c.processo}", it, "❌", "coverageError"))
        for it in c.semIdOrigem:
            linhas.append(linhaAudit57(f"↳↳ ORIGEM SEM ID_INTERNO — {c.processo}", it, "❌", "coverageError"))

    return linhas


def linhasValidacoesEstruturais57(coPeriodo: list[ItemBase], ctx: ContextoAuditoria) -> list[LinhaAudit]:
    linhas = []
    for item in ctx.somaSemContaOrdem or []:
        linhas.append(LinhaAudit(
            processo="VALIDAÇÃO SOMA",
            docSoma=item.docSoma or "",
            data=item.dataFmt or "",
            tipo=tipo57(item.tipo),
            desc=f"SOMA SEM CONTAORDEM | {txt57(item.desc)}",
            valor=valorFinanceiro57(item.tipo, item.valorRaw),
            status="❌",
            estilo="coverageError"
        ))

    for x in docsReutilizadosPeriodo57(coPeriodo, ctx):
        linhas.append(LinhaAudit(
            processo="VALIDAÇÃO DOC. SOMA",
            docSoma=x["doc"],
            tipo="CONTROLE",
            desc=f"DOC. SOMA REUTILIZADO | CONTAORDEM={x['qtdCO']} | SOMA={x['qtdSoma']}",
            status="❌",
            estilo="coverageError"
        ))

    for item in coPeriodo or []:
        probs = ctx.seqProblemas.get(item.linhaFonte, [])
        if not probs:
            continue
        linhas.append(LinhaAudit(
            processo="VALIDAÇÃO DESCRIÇÃO SOMA",
            idInterno=item.idInterno or "",
            docSoma=item.docSoma or "",
            data=item.dataFmt or "",
            tipo=tipo57(item.tipo),
            desc=f"{txt57(item.descSoma)} | {' | '.join(probs)}",
            valor=valorFinanceiro57(item.tipo, item.valorRaw),
            status="🟡",
            estilo="cardPending"
        ))

    return linhas


# ============================================================================
# AUDITORIA INDIVIDUAL (NORMAL, MVV, CARTÃO)
# ============================================================================

def auditarNormal57(co: ItemBase, ctx: ContextoAuditoria) -> dict:
    procRaw = txt57(co.processoProx)
    procKey = proc57(processoCanonico57(procRaw))
    dispensaSoma = dispensaSomaAgregado57(co)

    origem = ctx.origem(procRaw)
    matchOrigem = matchId57(origem.get("porId") if (origem and not origem.get("erro")) else None, co.idInterno)

    matchSoma = (
        {"item": None, "duplicado": False, "usoCO": 0, "usoSoma": 0, "cardinalidadeInsuficiente": False, "ambiguo": False}
        if dispensaSoma else matchSomaParaCO57(co, ctx)
    )

    status = "✅"
    motivos = []

    if not procRaw:
        status = "❌"
        motivos.append("PROCESSO_VAZIO")

    if origem and origem.get("erro"):
        status = "❌"
        motivos.append(f"ORIGEM_INDISPONIVEL={origem['erro']}")
    elif not origem or not matchOrigem["item"]:
        status = "❌"
        motivos.append("ORIGEM_NAO_ENCONTRADA_POR_ID")
    elif matchOrigem["duplicado"] and status != "❌":
        status = "🟡"
        motivos.append("ID_DUPLICADO_NA_ORIGEM")

    if not dispensaSoma:
        if not ehDocSomaReal57(co.docSoma):
            status = "❌"
            motivos.append("DOC_SOMA_INVALIDO_OU_PENDENTE")
        elif not matchSoma["item"]:
            status = "❌"
            motivos.append("SOMA_NAO_ENCONTRADO")
        else:
            if matchSoma["cardinalidadeInsuficiente"]:
                status = "❌"
                motivos.append(f"DOC_SOMA_REUTILIZADO_CO(CO={matchSoma['usoCO']}|SOMA={matchSoma['usoSoma']})")
            elif matchSoma["duplicado"] and status != "❌":
                status = "🟡"
                motivos.append(f"DOC_SOMA_DUPLICADO_NA_SOMA={matchSoma['usoSoma']}")

            if matchSoma["ambiguo"] and status != "❌":
                status = "🟡"
                motivos.append("DOC_SOMA_MATCH_AMBIGUO")

    if matchOrigem["item"]:
        divs = compararCOOrigem57(co, matchOrigem["item"], procKey, ctx)
        if divs and status != "❌":
            status = "🟡"
        motivos.extend(divs)

    if matchSoma["item"]:
        divs = compararCOSoma57(co, matchSoma["item"], ctx.tol)
        if divs and status != "❌":
            status = "🟡"
        motivos.extend(divs)

    seq = ctx.seqProblemas.get(co.linhaFonte, [])
    if seq:
        if status == "✅":
            status = "🟡"
        motivos.extend(seq)

    linhas = [linhaAudit57("CONTAORDEM", co, status, "parent")]
    if not dispensaSoma:
        if matchSoma["item"]:
            linhas.append(linhaAudit57("↳↳ SOMA", dataclasses.replace(matchSoma["item"], idInterno=co.idInterno)))
        else:
            linhas.append(linhaVazia57("↳↳ SOMA", co.idInterno, co.docSoma))

    if matchOrigem["item"]:
        linhas.append(linhaAudit57(f"↳↳ {procRaw or 'ORIGEM'}", matchOrigem["item"]))
    else:
        linhas.append(linhaVazia57(f"↳↳ {procRaw or 'ORIGEM'}", co.idInterno, co.docSoma))

    return {
        "status": status,
        "motivo": " | ".join(motivos) or ("OK_SEM_SOMA_AGREGADO" if dispensaSoma else "OK"),
        "linhas": linhas
    }


def auditarMVVMensal57(ctx: ContextoAuditoria) -> dict:
    pagamentos = [x for x in (ctx.co or []) if ehMVV57(x)]
    origem = ctx.origem("T_EXTRATO")
    motivos = []
    status = "✅"

    referencia = ctx.periodo.dataInicial
    anoAnt = referencia.year if referencia.month > 1 else referencia.year - 1
    mesAnt = referencia.month - 1 if referencia.month > 1 else 12
    _, lastDayAnt = calendar.monthrange(anoAnt, mesAnt)
    iniMesAnt = datetime(anoAnt, mesAnt, 1, 0, 0, 0)
    fimMesAnt = datetime(anoAnt, mesAnt, lastDayAnt, 23, 59, 59)

    def noMesAnterior(x: ItemBase) -> bool:
        return bool(x and x.dataObj and iniMesAnt <= x.dataObj <= fimMesAnt)

    nomes15 = [
        "DIZIMOS 10%",
        "OFERTA COVV 2%",
        "OFERTA NOVAS OBRAS 2%",
        "OFERTA MISSOES 1%"
    ]

    detalhes15 = [x for x in (ctx.soma or []) if noMesAnterior(x) and cmp57(x.desc) in nomes15]
    encontrados15 = {cmp57(x.desc) for x in detalhes15}
    for nome in nomes15:
        if nome not in encontrados15:
            status = "❌"
            motivos.append(f"MVV_DETALHE_MES_ANTERIOR_AUSENTE={nome}")

    total15 = sum(abs57(x.valorRaw) for x in detalhes15)

    repassesLivraria = [x for x in (ctx.soma or []) if noMesAnterior(x) and cmp57(x.desc) == "REPASSE LIVRARIA 3%"]
    vendasLivraria = [
        x for x in (ctx.soma or [])
        if noMesAnterior(x) and tipoFinanceiroGeral57(x.tipo) == "ENTRADA" and "VENDA DE LIVROS (LIVRARIA)" in cmp57(x.desc)
    ]
    baseLivraria = sum(abs57(x.valorRaw) for x in vendasLivraria)
    repasseCalculado = round(baseLivraria * 3) / 100
    repasseLivraria = sum(abs57(x.valorRaw) for x in repassesLivraria) if repassesLivraria else repasseCalculado

    totalEsperado = zero57(total15 + repasseLivraria)
    totalPagoCO = sum(abs57(x.valorRaw) for x in pagamentos)
    totalExtrato = 0.0

    linhas = [
        linhaResumo57(
            "CONTAORDEM — MVV MENSAL",
            pagamentos[-1].dataFmt if pagamentos else "",
            "MVV",
            f"REPASSE REFERENTE A {mesAnt:02d}/{anoAnt}",
            -totalPagoCO,
            "",
            "parent"
        )
    ]

    for x in detalhes15:
        linhas.append(linhaAudit57("↳↳ SOMA — DETALHE 15% MÊS ANTERIOR", x, "", "mvv"))

    if repassesLivraria:
        for x in repassesLivraria:
            linhas.append(linhaAudit57("↳↳ SOMA — REPASSE LIVRARIA 3%", x, "", "mvv"))
    else:
        linhas.append(linhaResumo57(
            "↳↳ CÁLCULO REPASSE LIVRARIA 3%",
            fmtData57(fimMesAnt),
            "MVV",
            f"3% SOBRE VENDAS LIVRARIA={fmt57(baseLivraria)}",
            -repasseLivraria,
            "",
            "mvv"
        ))

    if not pagamentos:
        status = "❌"
        motivos.append("MVV_SEM_PAGAMENTO_CONTAORDEM")

    for pgto in pagamentos:
        matchOrig = matchId57(origem.get("porId") if (origem and not origem.get("erro")) else None, pgto.idInterno)
        linhas.append(linhaAudit57("↳↳ PAGAMENTO MVV — CONTAORDEM", dataclasses.replace(pgto, valorRaw=-abs57(pgto.valorRaw)), "", "mvv"))

        if origem and origem.get("erro"):
            status = "❌"
            motivos.append(f"T_EXTRATO_MVV_INDISPONIVEL={origem['erro']}")
            linhas.append(linhaVazia57("↳↳ PAGAMENTO MVV — T_EXTRATO", pgto.idInterno, pgto.docSoma))
            continue

        if not matchOrig["item"]:
            status = "❌"
            motivos.append(f"T_EXTRATO_MVV_NAO_ENCONTRADO={pgto.idInterno or '?'}")
            linhas.append(linhaVazia57("↳↳ PAGAMENTO MVV — T_EXTRATO", pgto.idInterno, pgto.docSoma))
            continue

        totalExtrato += abs57(matchOrig["item"].valorRaw)
        if matchOrig["duplicado"]:
            if status != "❌":
                status = "🟡"
            motivos.append(f"ID_MVV_DUPLICADO_T_EXTRATO={pgto.idInterno or '?'}")

        if not eq57(abs57(matchOrig["item"].valorRaw), abs57(pgto.valorRaw), ctx.tol):
            if status != "❌":
                status = "🟡"
            motivos.append(f"VALOR_MVV_T_EXTRATO_DIVERGE={pgto.idInterno or '?'}")

        linhas.append(linhaAudit57("↳↳ PAGAMENTO MVV — T_EXTRATO", matchOrig["item"], "", "mvv"))

    if not eq57(totalPagoCO, totalExtrato, ctx.tol):
        if status != "❌":
            status = "🟡"
        motivos.append(f"TOTAL_MVV_CO={fmt57(totalPagoCO)}_T_EXTRATO={fmt57(totalExtrato)}")

    if not eq57(totalEsperado, totalPagoCO, ctx.tol):
        if status != "❌":
            status = "🟡"
        motivos.append(f"TOTAL_MVV_ESPERADO={fmt57(totalEsperado)}_PAGO={fmt57(totalPagoCO)}_DIF={fmt57(totalEsperado - totalPagoCO)}")

    seqProblemas = []
    for x in pagamentos:
        seqProblemas.extend(ctx.seqProblemas.get(x.linhaFonte, []))
    if seqProblemas:
        if status == "✅":
            status = "🟡"
        motivos.extend(seqProblemas)

    linhas[0].status = status
    return {
        "status": status,
        "motivo": " | ".join(motivos) or "OK_MVV_MENSAL",
        "linhas": linhas
    }


def auditarCartao57(cicloKey: str, ctx: ContextoAuditoria) -> dict:
    ciclo = ctx.ciclosCartao.get(cicloKey)
    if not ciclo:
        return {
            "status": "❌",
            "motivo": f"CICLO_CARTAO_NAO_ENCONTRADO={cicloKey or '?'}",
            "linhas": [linhaResumo57("CONTAORDEM — CICLO CARTÃO", "", "CARTÃO", f"CICLO {cicloKey or '?'}", "", "❌", "parent")]
        }

    status = "✅"
    faltas = 0
    pendencias = 0
    divergencias = 0
    motivos = []
    linhas = []

    if ciclo.modoHistorico:
        if not ciclo.historicoEncontrado:
            faltas += 1
            motivos.append(f"SOMA_CARTAO_HISTORICO_NAO_FECHA={cicloKey}")
        elif ciclo.historicoAmbiguo:
            divergencias += 1
            motivos.append(f"SOMA_CARTAO_HISTORICO_AMBIGUO={cicloKey}")
    elif not ciclo.componentes:
        faltas += 1
        motivos.append(f"SEM_COMPONENTES_CARTAO_CICLO={cicloKey}")

    linhas.append(linhaResumo57("CONTAORDEM — CICLO CARTÃO", ciclo.dataReferenciaFmt, "CARTÃO", f"CICLO {cicloKey}", -ciclo.pagamentosTotal, "", "parent"))

    origemExtrato = ctx.origem("T_EXTRATO")

    # 1) Cada pagamento bancário
    for pgto in ciclo.pagamentos:
        matchExtrato = matchId57(origemExtrato.get("porId") if (origemExtrato and not origemExtrato.get("erro")) else None, pgto.idInterno)
        linhas.append(linhaAudit57("↳↳ PAGAMENTO CARTÃO — CONTAORDEM", dataclasses.replace(pgto, valorRaw=-abs57(pgto.valorRaw)), "", "cardPayment"))

        if not matchExtrato["item"]:
            faltas += 1
            motivos.append(f"T_EXTRATO_PGTO_CARTAO_NAO_ENCONTRADO={pgto.idInterno or '?'}")
            linhas.append(linhaVazia57("↳↳ PAGAMENTO CARTÃO — T_EXTRATO", pgto.idInterno, pgto.docSoma, "cardPending"))
        else:
            if matchExtrato["duplicado"]:
                divergencias += 1
                motivos.append(f"ID_PGTO_CARTAO_DUPLICADO_T_EXTRATO={pgto.idInterno or '?'}")
            if not eq57(num57(matchExtrato["item"].valorRaw), -abs57(pgto.valorRaw), ctx.tol):
                faltas += 1
                motivos.append(f"VALOR_PGTO_CARTAO_T_EXTRATO_DIVERGE={pgto.idInterno or '?'}")
            linhas.append(linhaAudit57("↳↳ PAGAMENTO CARTÃO — T_EXTRATO", matchExtrato["item"], "", "cardPayment"))

    # 2) Detalhes
    if ciclo.modoHistorico:
        for it in ciclo.componentesSomaHistorico:
            linhas.append(linhaAudit57("↳↳ SOMA CARTÃO — DETALHE HISTÓRICO", it, "", "card"))
    else:
        for saida in ciclo.componentes:
            i_val = id57(saida.idInterno)
            finEsperado = tipoFinanceiroSaidas57(saida.tipo)
            credito = (finEsperado == "ENTRADA")

            matchCO = matchCOProcId57(ctx.idxCO, ["SAÍDA", "SAÍDAS"], saida.idInterno)
            filhoCO = matchCO["item"]
            matchSoma = matchSomaParaCO57(filhoCO, ctx) if filhoCO else {
                "item": None, "duplicado": False, "usoCO": 0, "usoSoma": 0,
                "cardinalidadeInsuficiente": False, "ambiguo": False
            }

            if not finEsperado:
                faltas += 1
                motivos.append(f"TIPO_SAIDAS_NAO_MAPEADO={i_val or saida.tipo or '?'}")

            if not filhoCO:
                faltas += 1
                motivos.append(f"CONTAORDEM_FILHO_NAO_ENCONTRADO={i_val or '?'}")
            else:
                if matchCO["duplicado"]:
                    divergencias += 1
                    motivos.append(f"CONTAORDEM_FILHO_DUPLICADO={i_val or '?'}")
                if doc57(filhoCO.docSoma) != doc57(saida.docSoma):
                    divergencias += 1
                    motivos.append(f"DOC_FILHO_DIVERGE={i_val or '?'}")
                if not eq57(abs57(filhoCO.valorRaw), abs57(saida.valorRaw), ctx.tol):
                    divergencias += 1
                    motivos.append(f"VALOR_FILHO_DIVERGE={i_val or '?'}")
                if finEsperado and tipoFinanceiroGeral57(filhoCO.tipo) != finEsperado:
                    divergencias += 1
                    motivos.append(f"TIPO_FILHO_DIVERGE={i_val or '?'}")
                if filhoCO.dataFmt and saida.dataFmt and filhoCO.dataFmt != saida.dataFmt:
                    divergencias += 1
                    motivos.append(f"DATA_FILHO_DIVERGE={i_val or '?'}")

                seq = ctx.seqProblemas.get(filhoCO.linhaFonte, [])
                if seq:
                    divergencias += len(seq)
                    motivos.extend([f"{x}={i_val or '?'}" for x in seq])

            if not ehDocSomaReal57(saida.docSoma):
                faltas += 1
                motivos.append(f"SAIDA_SEM_DOC_SOMA_REAL={i_val or '?'}")
            elif not matchSoma["item"]:
                faltas += 1
                motivos.append(f"DOC_NAO_LOCALIZADO_NO_SOMA={saida.docSoma}")
            else:
                if matchSoma["cardinalidadeInsuficiente"]:
                    faltas += 1
                    motivos.append(f"DOC_SOMA_REUTILIZADO_CO={saida.docSoma}(CO={matchSoma['usoCO']}|SOMA={matchSoma['usoSoma']})")
                elif matchSoma["duplicado"]:
                    divergencias += 1
                    motivos.append(f"DOC_SOMA_DUPLICADO_NA_SOMA={saida.docSoma}")

                if matchSoma["ambiguo"]:
                    divergencias += 1
                    motivos.append(f"DOC_SOMA_MATCH_AMBIGUO={saida.docSoma}")

                if filhoCO:
                    divSoma = compararCOSoma57(filhoCO, matchSoma["item"], ctx.tol)
                    if divSoma:
                        divergencias += len(divSoma)
                        motivos.extend([f"{x}={i_val or '?'}" for x in divSoma])

            estilo = "cardCredit" if credito else "card"
            estiloPend = "cardCreditPending" if credito else "cardPending"

            if filhoCO:
                linhas.append(linhaAudit57("↳↳ CONTAORDEM CARTÃO — CRÉDITO" if credito else "↳↳ CONTAORDEM CARTÃO", filhoCO, "", estilo))
            else:
                linhas.append(linhaVazia57("↳↳ CONTAORDEM CARTÃO — CRÉDITO" if credito else "↳↳ CONTAORDEM CARTÃO", saida.idInterno, saida.docSoma, estiloPend))

            if matchSoma["item"]:
                linhas.append(linhaAudit57("↳↳ SOMA CARTÃO — CRÉDITO" if credito else "↳↳ SOMA CARTÃO", dataclasses.replace(matchSoma["item"], idInterno=saida.idInterno), "", estilo))
            else:
                linhas.append(linhaVazia57("↳↳ SOMA CARTÃO — CRÉDITO" if credito else "↳↳ SOMA CARTÃO", saida.idInterno, saida.docSoma, estiloPend))

            linhas.append(linhaAudit57(
                "↳↳ SAÍDAS CARTÃO — CRÉDITO" if credito else "↳↳ SAÍDAS CARTÃO",
                dataclasses.replace(saida, dataFmt=saida.dataValorFmt or saida.dataFmt),
                "",
                estilo
            ))

    # 3) Reconciliação
    linhas.append(linhaResumo57("↳↳ SALDO INICIAL / CARRY CARTÃO", ciclo.dataReferenciaFmt, "CARTÃO", "SALDO BRUTO TRAZIDO DO CICLO ANTERIOR", ciclo.saldoInicial, "", "cardSummary"))
    linhas.append(linhaResumo57("↳↳ TOTAL PAGAMENTOS CARTÃO", ciclo.dataReferenciaFmt, "CARTÃO", "PAGAMENTOS BANCÁRIOS DO CICLO", ciclo.pagamentosTotal, "", "cardSummary"))
    linhas.append(linhaResumo57("↳↳ TOTAL DESPESAS BRUTAS CARTÃO", ciclo.dataReferenciaFmt, "CARTÃO", "DETALHES HISTÓRICOS LOCALIZADOS NO SOMA" if ciclo.modoHistorico else "DETALHES DO EXTRATO / SAÍDAS", -ciclo.despesasBrutas, "", "cardSummary"))
    if ciclo.creditos > 0:
        linhas.append(linhaResumo57("↳↳ TOTAL CRÉDITOS/ESTORNOS CARTÃO", ciclo.dataReferenciaFmt, "CARTÃO", "CRÉDITOS CONTROLADOS SEPARADAMENTE", ciclo.creditos, "", "cardCreditSummary"))
    linhas.append(linhaResumo57("↳↳ TOTAL LÍQUIDO ECONÔMICO CARTÃO", ciclo.dataReferenciaFmt, "CARTÃO", "DESPESAS BRUTAS - CRÉDITOS/ESTORNOS", -(ciclo.despesasBrutas - ciclo.creditos), "", "cardSummary"))
    linhas.append(linhaResumo57(
        "↳↳ SALDO FINAL / CARRY CARTÃO",
        ciclo.dataReferenciaFmt,
        "CARTÃO",
        "SALDO A TRANSPORTAR PARA O PRÓXIMO CICLO" if ciclo.temProximo else "SALDO BRUTO APÓS O CICLO",
        ciclo.saldoFinal,
        "",
        "cardSummary" if abs(ciclo.saldoFinal) <= ctx.tol else "pendingSummary"
    ))

    if faltas > 0:
        status = "❌"
    elif divergencias > 0 or pendencias > 0:
        status = "🟡"

    if status != "❌" and not ciclo.ledgerConfiavel and not ciclo.fechoDireto:
        status = "🟡"
        motivos.append("LEDGER_CARTAO_SEM_ANCORA_CONFIAVEL")

    if status != "❌" and abs(ciclo.saldoFinal) > ctx.tol and not ciclo.temProximo:
        status = "🟡"
        motivos.append(f"SALDO_CARTAO_EM_ABERTO={fmt57(ciclo.saldoFinal)}")
    elif abs(ciclo.saldoFinal) > ctx.tol:
        motivos.append(f"CARRY_FORWARD={fmt57(ciclo.saldoFinal)}")

    linhas[0].status = status
    return {
        "status": status,
        "motivo": " | ".join(motivos) or "OK_CARTAO_CICLO",
        "linhas": linhas
    }


# ============================================================================
# BALANCETE DO SOMA
# ============================================================================

@dataclass
class OrigemValor:
    rotulo: str
    valor: float
    estilo: str


@dataclass
class BalanceteBloco:
    soma: float = 0.0
    contaOrdem: float = 0.0
    origens: list[OrigemValor] = field(default_factory=list)
    totalOrigens: float = 0.0
    diffSomaCO: float = 0.0
    diffCOOrigens: float = 0.0
    diffSomaOrigens: float = 0.0


@dataclass
class Balancete:
    entrada: BalanceteBloco = field(default_factory=BalanceteBloco)
    saida: BalanceteBloco = field(default_factory=BalanceteBloco)


def calcularBalancete57(co: list[ItemBase], soma: list[ItemBase], saidas: list[ItemBase], ctx: ContextoAuditoria) -> Balancete:
    def noPeriodo(x: ItemBase) -> bool:
        return noPeriodo57(x, ctx.periodo)

    extrato = ctx.origem("T_EXTRATO")
    dizimos = ctx.origem("DÍZIMOS/OFERTAS")
    vendas = ctx.origem("VC_VENDAS")
    financeiro = ctx.origem("FINANCEIRO")

    itensExtrato = [x for x in extrato["itens"] if noPeriodo(x)] if extrato and not extrato.get("erro") else []
    itensDizimos = [x for x in dizimos["itens"] if noPeriodo(x)] if dizimos and not dizimos.get("erro") else []
    itensVendas = [x for x in vendas["itens"] if noPeriodo(x)] if vendas and not vendas.get("erro") else []
    itensFinanceiro = [x for x in financeiro["itens"] if noPeriodo(x)] if financeiro and not financeiro.get("erro") else []

    # ENTRADAS
    totalEntradasSoma = sum(abs57(x.valorRaw) for x in soma if noPeriodo(x) and tipoFinanceiroGeral57(x.tipo) == "ENTRADA")
    totalEntradasContaOrdem = sum(abs57(x.valorRaw) for x in co if tipoFinanceiroGeral57(x.tipo) == "ENTRADA")
    entradaExtrato = sum(abs57(x.valorRaw) for x in itensExtrato if tipoFinanceiroExtrato57(x) == "ENTRADA")
    entradaDizimos = sum(abs57(x.valorRaw) for x in itensDizimos if tipoOrigem57(proc57("DÍZIMOS/OFERTAS"), x, ctx.CONFIG_TIPOS) == "ENTRADA")
    entradaCafe = sum(abs57(x.valorRaw) for x in itensVendas if cmp57(x.formaPagamento) == "DINHEIRO")
    entradaSaidas = sum(abs57(x.valorRaw) for x in saidas if noPeriodo(x) and tipoFinanceiroSaidas57(x.tipo) == "ENTRADA")

    origensEntrada = [
        OrigemValor("ENTRADA (EXTRATO)", entradaExtrato, "orig"),
        OrigemValor("ENTRADA (DÍZIMOS/OFERTAS)", entradaDizimos, "orig"),
        OrigemValor("ENTRADA (VERBO CAFÉ)", entradaCafe, "orig"),
        OrigemValor("ENTRADA (SAÍDAS) - ESTORNOS", entradaSaidas, "credit")
    ]
    totalOrigensEntrada = sum(x.valor for x in origensEntrada)

    blocoEntrada = BalanceteBloco(
        soma=totalEntradasSoma,
        contaOrdem=totalEntradasContaOrdem,
        origens=origensEntrada,
        totalOrigens=totalOrigensEntrada,
        diffSomaCO=zero57(totalEntradasSoma - totalEntradasContaOrdem),
        diffCOOrigens=zero57(totalEntradasContaOrdem - totalOrigensEntrada),
        diffSomaOrigens=zero57(totalEntradasSoma - totalOrigensEntrada)
    )

    # SAÍDAS
    totalSaidasSoma = -sum(abs57(x.valorRaw) for x in soma if noPeriodo(x) and tipoFinanceiroGeral57(x.tipo) == "SAÍDA" and not ehDerivadoMVVSoma57(x))
    # REGRA CRÍTICA v57: TOTAL SAÍDAS (CONTAORDEM) considera EXCLUSIVAMENTE tipoFinanceiroGeral == 'SAÍDA' (TIPO=CARTÃO nunca entra!)
    totalSaidasContaOrdem = -sum(abs57(x.valorRaw) for x in co if tipoFinanceiroGeral57(x.tipo) == "SAÍDA")

    saidaExtrato = -sum(abs57(x.valorRaw) for x in itensExtrato if tipoFinanceiroExtrato57(x) == "SAÍDA")
    saidaSaidas = -sum(abs57(x.valorRaw) for x in saidas if noPeriodo(x) and tipoFinanceiroSaidas57(x.tipo) == "SAÍDA")
    pagamentoCafe = -sum(abs57(x.valorRaw) for x in itensFinanceiro)

    origensSaida = [
        OrigemValor("SAÍDA (EXTRATO)", saidaExtrato, "orig"),
        OrigemValor("SAÍDA (SAÍDAS)", saidaSaidas, "orig"),
        OrigemValor("PAGAMENTO (VERBO CAFÉ)", pagamentoCafe, "orig")
    ]
    totalOrigensSaida = sum(x.valor for x in origensSaida)

    blocoSaida = BalanceteBloco(
        soma=totalSaidasSoma,
        contaOrdem=totalSaidasContaOrdem,
        origens=origensSaida,
        totalOrigens=totalOrigensSaida,
        diffSomaCO=zero57(totalSaidasSoma - totalSaidasContaOrdem),
        diffCOOrigens=zero57(totalSaidasContaOrdem - totalOrigensSaida),
        diffSomaOrigens=zero57(totalSaidasSoma - totalOrigensSaida)
    )

    return Balancete(entrada=blocoEntrada, saida=blocoSaida)


# ============================================================================
# CONTEXTO DE EXECUÇÃO
# ============================================================================

class ContextoAuditoria:
    def __init__(
        self,
        periodo: Periodo,
        data_manager: SheetsDataManager,
        tol: float = TOL
    ):
        self.periodo = periodo
        self.data_manager = data_manager
        self.tol = tol
        self.exclusoes = data_manager.exclusoes_normalizadas
        self.CONFIG_EXT = CONFIG_EXT
        self.CONFIG_TIPOS = CONFIG_TIPOS

        # CONTAORDEM
        self.coAllRaw = data_manager.parsed_cache["CONTAORDEM_RAW"]
        self.coAll = [x for x in self.coAllRaw if not descricaoExcluida57(x.desc, self.exclusoes)]
        self.coRawPeriodo = [x for x in self.coAllRaw if noPeriodo57(x, self.periodo)]
        self.co = [x for x in self.coAll if noPeriodo57(x, self.periodo)]

        # SOMA e SAÍDAS
        self.soma = data_manager.parsed_cache["SOMA"]
        self.saidas = data_manager.parsed_cache["SAÍDAS"]

        # Índices
        self.idxSoma = idxDoc57(self.soma)
        self.idxCO = idxCOProcId57(self.coAll)
        self.idxCODoc = idxDoc57(self.coAll)

        # Cache de origens
        self.cache_origens: dict[str, dict] = {}
        self.cache_origens[proc57("SAÍDAS")] = {
            "itens": self.saidas,
            "porId": idxId57(self.saidas),
            "erro": ""
        }

        # Sequencial e ciclos
        self.seqProblemas = validarSequencialDescricao57(self.coAll)
        self.ciclosCartao = construirCiclosCartao57(self.coAll, self.saidas, self.soma, self.tol)
        self.somaSemContaOrdem = somaSemContaOrdemPeriodo57(self)

    def origem(self, processoRaw: str) -> Optional[dict]:
        canon = processoCanonico57(processoRaw)
        key = proc57(canon or processoRaw)
        if not key:
            return None
        if key in self.cache_origens:
            return self.cache_origens[key]

        itens = self.data_manager.parsed_cache.get(canon) or self.data_manager.parsed_cache.get(key)
        if itens is not None:
            res = {"itens": itens, "porId": idxId57(itens), "erro": ""}
        else:
            res = {"itens": [], "porId": {}, "erro": f"SHEET_NAO_ENCONTRADA={processoRaw}"}
        self.cache_origens[key] = res
        return res


# ============================================================================
# EXECUÇÃO DA AUDITORIA PARA UM MÊS
# ============================================================================

@dataclass
class ResumoMesAudit:
    periodo: Periodo
    ok: int = 0
    amarelo: int = 0
    erro: int = 0
    falhasCobertura: int = 0
    totalGrupos: int = 0
    balancete: Optional[Balancete] = None
    cobertura: Optional[CoberturaGeral] = None
    linhasDetalhe: list[LinhaAudit] = field(default_factory=list)
    linhasBalancete: list[list[Any]] = field(default_factory=list)
    problemasDetalhados: list[dict] = field(default_factory=list)


def auditarMes57(ctx: ContextoAuditoria) -> ResumoMesAudit:
    cobertura = validarCoberturaOrigens57(ctx.coRawPeriodo, ctx)

    ciclosCartaoPeriodo = {
        cicloPagamentoCartao57(x)
        for x in ctx.co if ehPgtoCartao57(x) and cicloPagamentoCartao57(x)
    }

    idsFilhosCartaoPeriodo = set()
    for chave in ciclosCartaoPeriodo:
        ciclo = ctx.ciclosCartao.get(chave)
        if ciclo:
            for item in ciclo.componentes:
                i_val = id57(item.idInterno)
                if i_val:
                    idsFilhosCartaoPeriodo.add(i_val)

    detalhe: list[LinhaAudit] = []
    detalhe.extend(linhasCobertura57(cobertura))
    detalhe.extend(linhasValidacoesEstruturais57(ctx.co, ctx))
    if detalhe:
        detalhe.append(linhaSeparador57())

    ok_count = 0
    amarelo_count = 0
    erro_count = 0
    grupos_count = 0

    ciclosProcessados = set()
    mvvProcessado = False
    problemasDetalhados = []

    for item in ctx.co:
        i_val = id57(item.idInterno)
        proc = processoCanonico57(item.processoProx)

        if i_val and i_val in idsFilhosCartaoPeriodo and proc == "SAÍDAS" and not ehPgtoCartao57(item):
            continue

        if ehMVV57(item):
            if mvvProcessado:
                continue
            mvvProcessado = True
            resultado = auditarMVVMensal57(ctx)
        elif ehPgtoCartao57(item):
            cicloKey = cicloPagamentoCartao57(item)
            if cicloKey in ciclosProcessados:
                continue
            ciclosProcessados.add(cicloKey)
            resultado = auditarCartao57(cicloKey, ctx)
        else:
            resultado = auditarNormal57(item, ctx)

        detalhe.extend(resultado["linhas"])
        detalhe.append(linhaSeparador57())
        grupos_count += 1

        st = resultado["status"]
        if st == "✅":
            ok_count += 1
        elif st == "🟡":
            amarelo_count += 1
            problemasDetalhados.append({
                "status": "🟡",
                "idInterno": item.idInterno,
                "docSoma": item.docSoma,
                "data": item.dataFmt,
                "processo": item.processoProx,
                "motivo": resultado["motivo"]
            })
        else:
            erro_count += 1
            problemasDetalhados.append({
                "status": "❌",
                "idInterno": item.idInterno,
                "docSoma": item.docSoma,
                "data": item.dataFmt,
                "processo": item.processoProx,
                "motivo": resultado["motivo"]
            })

    # Cobertura falhas adicionais
    for cob in cobertura.processos:
        for f in cob.faltantes:
            problemasDetalhados.append({
                "status": "❌",
                "tipo": "COBERTURA_FALTANTE",
                "processo": cob.processo,
                "idInterno": f.idInterno,
                "docSoma": f.docSoma,
                "data": f.dataFmt,
                "motivo": f"ORIGEM SEM CONTAORDEM — {cob.processo}"
            })
        for e in cob.extras:
            problemasDetalhados.append({
                "status": "❌",
                "tipo": "COBERTURA_EXTRA",
                "processo": cob.processo,
                "idInterno": e.idInterno,
                "docSoma": e.docSoma,
                "data": e.dataFmt,
                "motivo": f"CONTAORDEM SEM ORIGEM — {cob.processo}"
            })

    balancete = calcularBalancete57(ctx.co, ctx.soma, ctx.saidas, ctx)
    falhasCobTotal = cobertura.totalFalhas + len(ctx.somaSemContaOrdem or [])

    # Montar linhas de balancete (A:B)
    linhasBal = gerarLinhasBalancete(
        balancete=balancete,
        statusOk=ok_count,
        statusAmarelo=amarelo_count,
        statusErro=erro_count,
        falhasCobertura=falhasCobTotal,
        cobertura=cobertura,
        somaSemCO=len(ctx.somaSemContaOrdem or []),
        docsReutilizados=len(docsReutilizadosPeriodo57(ctx.co, ctx)),
        seqInvalidos=sum(1 for x in ctx.co if x.linhaFonte in ctx.seqProblemas),
        exclusoes=ctx.data_manager.exclusoes_originais,
        tol=ctx.tol
    )

    return ResumoMesAudit(
        periodo=ctx.periodo,
        ok=ok_count,
        amarelo=amarelo_count,
        erro=erro_count,
        falhasCobertura=falhasCobTotal,
        totalGrupos=grupos_count,
        balancete=balancete,
        cobertura=cobertura,
        linhasDetalhe=detalhe,
        linhasBalancete=linhasBal,
        problemasDetalhados=problemasDetalhados
    )


def gerarLinhasBalancete(
    balancete: Balancete,
    statusOk: int,
    statusAmarelo: int,
    statusErro: int,
    falhasCobertura: int,
    cobertura: CoberturaGeral,
    somaSemCO: int,
    docsReutilizados: int,
    seqInvalidos: int,
    exclusoes: list[str],
    tol: float
) -> list[list[Any]]:
    def styleDiff(v: float) -> str:
        return "diffOk" if eq57(v, 0.0, tol) else "diffErr"

    rows = [
        ["BALANCETE DO SOMA", "", "head"],
        ["ENTRADAS", "", "inhead"],
        ["TOTAL ENTRADAS (SOMA)", balancete.entrada.soma, "totalMain"],
        ["TOTAL ENTRADAS (CONTAORDEM)", balancete.entrada.contaOrdem, "totalCO"],
        ["ORIGENS DAS ENTRADAS", balancete.entrada.totalOrigens, "origensHead"],
    ]
    for x in balancete.entrada.origens:
        rows.append([x.rotulo, x.valor, x.estilo])
    rows.extend([
        ["DIF. ENTRADAS (SOMA ↔ CONTAORDEM)", balancete.entrada.diffSomaCO, styleDiff(balancete.entrada.diffSomaCO)],
        ["DIF. ENTRADAS (CONTAORDEM ↔ ORIGENS)", balancete.entrada.diffCOOrigens, styleDiff(balancete.entrada.diffCOOrigens)],
        ["DIF. ENTRADAS (SOMA ↔ ORIGENS)", balancete.entrada.diffSomaOrigens, styleDiff(balancete.entrada.diffSomaOrigens)],
        ["", "", "blank"],
        ["SAÍDAS", "", "outhead"],
        ["TOTAL SAÍDAS (SOMA)", balancete.saida.soma, "totalMainOut"],
        ["TOTAL SAÍDAS (CONTAORDEM)", balancete.saida.contaOrdem, "totalCOOut"],
        ["ORIGENS DAS SAÍDAS", balancete.saida.totalOrigens, "origensHead"],
    ])
    for x in balancete.saida.origens:
        rows.append([x.rotulo, x.valor, x.estilo])
    rows.extend([
        ["DIF. SAÍDAS (SOMA ↔ CONTAORDEM)", balancete.saida.diffSomaCO, styleDiff(balancete.saida.diffSomaCO)],
        ["DIF. SAÍDAS (CONTAORDEM ↔ ORIGENS)", balancete.saida.diffCOOrigens, styleDiff(balancete.saida.diffCOOrigens)],
        ["DIF. SAÍDAS (SOMA ↔ ORIGENS)", balancete.saida.diffSomaOrigens, styleDiff(balancete.saida.diffSomaOrigens)],
        ["", "", "blank"],
        ["STATUS PROCESSAMENTO", "", "statushead"],
        ["✅ CONCILIADOS", statusOk, "okCount"],
        ["🟡 PENDENTES", statusAmarelo, "pendCount"],
        ["❌ NÃO CONCILIADOS", statusErro, "errCount"],
        ["❌ FALHAS DE COBERTURA", falhasCobertura, "errCount" if falhasCobertura else "okCount"],
        ["", "", "blank"],
        ["VALIDAÇÕES ESTRUTURAIS", "", "validationHead"],
        ["SOMA SEM CONTAORDEM", somaSemCO, "errCount" if somaSemCO else "okCount"],
        ["DOC. SOMA REUTILIZADO (CO > SOMA)", docsReutilizados, "errCount" if docsReutilizados else "okCount"],
        ["DESCRIÇÃO SOMA — SEQUENCIAL INVÁLIDO", seqInvalidos, "pendCount" if seqInvalidos else "okCount"],
        ["", "", "blank"],
        ["COBERTURA DAS ORIGENS", "", "coverageHead"],
    ])

    for c in cobertura.processos or []:
        ok = (
            not c.erro and not c.faltantes and not c.extras and
            not c.duplicadosCO and not c.duplicadosOrigem and
            not c.semIdCO and not c.semIdOrigem
        )
        rows.extend([
            [f"{c.processo} — CONTAORDEM", c.qtdCO, "count"],
            [f"{c.processo} — ORIGEM", c.qtdOrigem, "count"],
            [f"{c.processo} — FALTANTES NA CONTAORDEM", len(c.faltantes), "errCount" if c.faltantes else "okCount"],
            [f"{c.processo} — EXTRAS NA CONTAORDEM", len(c.extras), "errCount" if c.extras else "okCount"],
            [f"{c.processo} — DUPLICADOS", len(c.duplicadosCO) + len(c.duplicadosOrigem), "okCount" if ok else "pendCount"],
        ])
        if c.erro:
            rows.append([f"{c.processo} — ERRO DE LEITURA", c.erro, "textErr"])

    rows.extend([
        ["", "", "blank"],
        ["EXCLUIR DO PROCESSAMENTO", "", "exhead"],
    ])
    for ex in exclusoes or []:
        rows.append([ex, "", "ex"])

    return rows


# ============================================================================
# GRAVAÇÃO NA SHEET T_AUDIT (OPCIONAL VIA --gravar-sheet)
# ============================================================================

def gravarAuditSheet57(gc: gspread.Client, spreadsheet_url: str, resumo: ResumoMesAudit):
    logger.info(f"Gravando auditoria de {resumo.periodo.mesTexto}/{resumo.periodo.ano} na sheet T_AUDIT...")
    sh = gc.open_by_url(spreadsheet_url)
    ws = sh.worksheet("T_AUDIT")

    # 1. Atualizar A1:B1 com ano e mês
    ws.update(values=[[str(resumo.periodo.ano), resumo.periodo.mesTexto]], range_name="A1:B1")

    # 2. Balancete A2:B...
    bal_values = [[r[0], r[1]] for r in resumo.linhasBalancete]
    ws.update(values=bal_values, range_name=f"A2:B{len(bal_values) + 1}")

    # 3. Detalhe D2:K...
    detalhe_rows = [
        ["PROCESSO", "ID_INTERNO", "DOC. SOMA", "DATA", "TIPO", "DESCRIÇÃO", "VALOR", "STATUS"]
    ]
    for x in resumo.linhasDetalhe:
        detalhe_rows.append([
            x.processo, x.idInterno, x.docSoma, x.data, x.tipo, x.desc, x.valor, x.status
        ])

    # Limpar linhas antigas de detalhe e atualizar
    num_rows = len(detalhe_rows)
    ws.update(values=detalhe_rows, range_name=f"D2:K{num_rows + 1}")
    logger.info(f"Gravação concluída na sheet T_AUDIT ({num_rows} linhas de auditoria).")


# ============================================================================
# MOTOR DE EXECUÇÃO MULTI-MÊS / RELATÓRIO
# ============================================================================

def executar_auditoria_intervalo(
    data_manager: SheetsDataManager,
    ano_inicio: int = 2024,
    mes_inicio: int = 1,
    ano_fim: int = 2026,
    mes_fim: int = 9,
    exportar_json: Optional[str] = None
) -> list[ResumoMesAudit]:
    logger.info(f"Iniciando auditoria mês a mês: {mes_inicio:02d}/{ano_inicio} até {mes_fim:02d}/{ano_fim}...")
    resultados = []

    # Iterar pelos meses
    ano_atual = ano_inicio
    mes_atual = mes_inicio
    while (ano_atual < ano_fim) or (ano_atual == ano_fim and mes_atual <= mes_fim):
        periodo = criar_periodo(ano_atual, mes_atual)
        ctx = ContextoAuditoria(periodo=periodo, data_manager=data_manager, tol=TOL)
        resumo = auditarMes57(ctx)
        resultados.append(resumo)

        # Log sintético do mês
        b = resumo.balancete
        dif_ent = b.entrada.diffSomaCO if b else 0.0
        dif_sai = b.saida.diffSomaCO if b else 0.0
        status_geral = "✅ OK" if resumo.erro == 0 and resumo.falhasCobertura == 0 and abs(dif_ent) <= TOL and abs(dif_sai) <= TOL else "⚠️ COM DIVERGÊNCIAS"

        logger.info(
            f"[{periodo.mesTexto[:3]}/{periodo.ano}] Status: {status_geral} | "
            f"✅ {resumo.ok:3d} | 🟡 {resumo.amarelo:2d} | ❌ {resumo.erro:2d} | Falhas Cob: {resumo.falhasCobertura:2d} | "
            f"Dif Ent: {fmt57(dif_ent)} € | Dif Saí: {fmt57(dif_sai)} €"
        )

        # Próximo mês
        if mes_atual == 12:
            ano_atual += 1
            mes_atual = 1
        else:
            mes_atual += 1

    if exportar_json:
        exportar_relatorio_json(resultados, exportar_json)

    return resultados


def exportar_relatorio_json(resultados: list[ResumoMesAudit], caminho_arquivo: str):
    data_export = []
    for r in resultados:
        data_export.append({
            "mes": r.periodo.mes,
            "ano": r.periodo.ano,
            "mesTexto": r.periodo.mesTexto,
            "ok": r.ok,
            "amarelo": r.amarelo,
            "erro": r.erro,
            "falhasCobertura": r.falhasCobertura,
            "totalGrupos": r.totalGrupos,
            "balancete": {
                "entradas_soma": r.balancete.entrada.soma if r.balancete else 0,
                "entradas_co": r.balancete.entrada.contaOrdem if r.balancete else 0,
                "entradas_origens": r.balancete.entrada.totalOrigens if r.balancete else 0,
                "diff_entradas_soma_co": r.balancete.entrada.diffSomaCO if r.balancete else 0,
                "saidas_soma": r.balancete.saida.soma if r.balancete else 0,
                "saidas_co": r.balancete.saida.contaOrdem if r.balancete else 0,
                "saidas_origens": r.balancete.saida.totalOrigens if r.balancete else 0,
                "diff_saidas_soma_co": r.balancete.saida.diffSomaCO if r.balancete else 0,
            },
            "problemas": r.problemasDetalhados
        })
    with open(caminho_arquivo, "w", encoding="utf-8") as f:
        json.dump(data_export, f, indent=2, ensure_ascii=False)
    logger.info(f"Relatório exportado para {caminho_arquivo}")


def imprimir_resumo_execucao(resumo: ResumoMesAudit):
    p = resumo.periodo
    b = resumo.balancete
    print("\n" + "=" * 80)
    print(f"📊 RESULTADO DA AUDITORIA TESOURARIA (v57) — {p.mesTexto} / {p.ano}")
    print("=" * 80)

    print("\n--- STATUS DO PROCESSAMENTO ---")
    print(f"  ✅ Conciliados:          {resumo.ok:4d}")
    print(f"  🟡 Pendentes:            {resumo.amarelo:4d}")
    print(f"  ❌ Não Conciliados:      {resumo.erro:4d}")
    print(f"  ❌ Falhas de Cobertura:  {resumo.falhasCobertura:4d}")
    print(f"  📌 Total Grupos:         {resumo.totalGrupos:4d}")

    if b:
        print("\n--- BALANCETE DO SOMA (ENTRADAS) ---")
        print(f"  Total Entradas (SOMA):       {b.entrada.soma:>12.2f} €")
        print(f"  Total Entradas (CONTAORDEM): {b.entrada.contaOrdem:>12.2f} €")
        print(f"  Origens das Entradas:        {b.entrada.totalOrigens:>12.2f} €")
        for orig in b.entrada.origens:
            print(f"    - {orig.rotulo:<28}: {orig.valor:>10.2f} €")
        print(f"  DIF (SOMA ↔ CONTAORDEM):     {b.entrada.diffSomaCO:>12.2f} €")
        print(f"  DIF (CONTAORDEM ↔ ORIGENS):  {b.entrada.diffCOOrigens:>12.2f} €")
        print(f"  DIF (SOMA ↔ ORIGENS):        {b.entrada.diffSomaOrigens:>12.2f} €")

        print("\n--- BALANCETE DO SOMA (SAÍDAS) ---")
        print(f"  Total Saídas (SOMA):         {b.saida.soma:>12.2f} €")
        print(f"  Total Saídas (CONTAORDEM):   {b.saida.contaOrdem:>12.2f} €")
        print(f"  Origens das Saídas:          {b.saida.totalOrigens:>12.2f} €")
        for orig in b.saida.origens:
            print(f"    - {orig.rotulo:<28}: {orig.valor:>10.2f} €")
        print(f"  DIF (SOMA ↔ CONTAORDEM):     {b.saida.diffSomaCO:>12.2f} €")
        print(f"  DIF (CONTAORDEM ↔ ORIGENS):  {b.saida.diffCOOrigens:>12.2f} €")
        print(f"  DIF (SOMA ↔ ORIGENS):        {b.saida.diffSomaOrigens:>12.2f} €")

    if resumo.cobertura:
        print("\n--- COBERTURA DAS ORIGENS ---")
        print(f"{'Processo':<20} {'CO':>6} {'Origem':>8} {'Faltam':>8} {'Extras':>8} {'Dup.':>6}")
        print("-" * 60)
        for c in resumo.cobertura.processos:
            dup = len(c.duplicadosCO) + len(c.duplicadosOrigem)
            print(f"{c.processo:<20} {c.qtdCO:>6} {c.qtdOrigem:>8} {len(c.faltantes):>8} {len(c.extras):>8} {dup:>6}")

    if resumo.problemasDetalhados:
        print("\n--- LISTAGEM DE PENDÊNCIAS / DIVERGÊNCIAS ENCONTRADAS ---")
        print(f"{'St':<3} {'ID_INTERNO':<16} {'DOC.SOMA':<12} {'DATA':<12} {'PROCESSO':<15} {'MOTIVO'}")
        print("-" * 80)
        for p_item in resumo.problemasDetalhados:
            st = p_item.get("status", "")
            id_i = str(p_item.get("idInterno", ""))[:15]
            doc_s = str(p_item.get("docSoma", ""))[:11]
            dt = str(p_item.get("data", ""))[:10]
            proc = str(p_item.get("processo", ""))[:14]
            mot = p_item.get("motivo", "")
            print(f"{st:<3} {id_i:<16} {doc_s:<12} {dt:<12} {proc:<15} {mot}")
    print("=" * 80 + "\n")


# ============================================================================
# AUTO-CORREÇÃO E SINCRONIZAÇÃO ENTRE CONTAORDEM E ORIGENS
# ============================================================================

def col_letter(col_idx: int) -> str:
    res = ""
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        res = chr(65 + remainder) + res
    return res


MAPA_COLUNAS_SHEETS = {
    "CONTAORDEM": {"ID": 17, "DOC": 5, "DESC_SOMA": 10},
    "T_EXTRATO": {"ID": 9, "DOC": 1, "DESC_SOMA": 12},
    "DÍZIMOS/OFERTAS": {"ID": 15, "DOC": 6, "DESC_SOMA": 18},
    "SAÍDAS": {"ID": 1, "DOC": 6, "DESC_COMPRA": 9, "DESC_SOMA": 26},
    "Financeiro": {"ID": 1, "DOC": 10, "DESC_SOMA": 14},
    "VC_VENDAS": {"ID": 1, "DOC": 49, "DESC_SOMA": 53},
}


def aplicar_atualizacoes_coluna(ws, col_idx: int, updates_dict: dict[int, str]):
    """
    Grava atualizações em uma coluna de uma sheet de forma ultra-otimizada.
    """
    if not updates_dict:
        return
    col_let = col_letter(col_idx)
    num_updates = len(updates_dict)

    if num_updates <= 30:
        batch_data = [
            {"range": f"{col_let}{row}", "values": [[val]]}
            for row, val in updates_dict.items()
        ]
        ws.batch_update(batch_data, value_input_option="USER_ENTERED")
    else:
        min_row = min(updates_dict.keys())
        max_row = max(updates_dict.keys())
        range_name = f"{col_let}{min_row}:{col_let}{max_row}"
        current_vals = ws.get(range_name)
        total_needed = max_row - min_row + 1
        vals_list = []
        for i in range(total_needed):
            curr = current_vals[i][0] if i < len(current_vals) and len(current_vals[i]) > 0 else ""
            vals_list.append([curr])
        for row, val in updates_dict.items():
            idx = row - min_row
            vals_list[idx] = [val]
        ws.update(values=vals_list, range_name=range_name, value_input_option="USER_ENTERED")


def executar_autocorrecao_sincronia(
    data_manager: SheetsDataManager,
    aplicar_planilhas: bool = True
) -> dict:
    """
    Identifica e corrige inconsistências entre CONTAORDEM, SOMA e Sheets de Destino/Origem:
    
    1. Se em CONTAORDEM o TIPO for Entrada ou Saída e o DOC. SOMA tiver tamanho != 7 dígitos
       (ex: 'Analisar', 'Em processamento', vazio):
       - Busca na sheet do PROCESSO de origem (pelo ID_INTERNO). Se ela tiver um DOC. SOMA numérico de 7 dígitos,
         atualiza a CONTAORDEM.
       - Se a origem não tiver, busca no SOMA por valor, data e tipo. Se encontrar match único de 7 dígitos,
         atualiza a CONTAORDEM e a origem.
       - Atualiza também a DESCRIÇÃO SOMA com a descrição do SOMA.
       
    2. Sincronização Destino ↔ CONTAORDEM:
       - Para cada lançamento da CONTAORDEM com DOC. SOMA válido de 7 dígitos numéricos:
       - Localiza o registro correspondente pelo ID_INTERNO na sheet do PROCESSO
         (T_EXTRATO, DÍZIMOS/OFERTAS, SAÍDAS, FINANCEIRO, VC_VENDAS).
       - Se no destino o DOC. SOMA estiver diferente ou vazio, atualiza com o DOC. SOMA da CONTAORDEM.
       - Se no destino a DESCRIÇÃO SOMA estiver vazia ou diferente, atualiza com a DESCRIÇÃO SOMA da CONTAORDEM.
       
    3. Se em CONTAORDEM a DESCRIÇÃO SOMA estiver diferente da descrição no SOMA para um DOC real:
       - Atualiza a DESCRIÇÃO SOMA na CONTAORDEM (e replica no destino).
    """
    logger.info("Iniciando rotina de auto-correção e sincronização de DOC. SOMA e Descrição Soma...")

    # Mapeamentos de ID -> Item nas origens
    maps = {}
    for p in ["T_EXTRATO", "DÍZIMOS/OFERTAS", "SAÍDAS", "FINANCEIRO", "VC_VENDAS"]:
        if p in data_manager.parsed_cache:
            maps[p] = {x.idInterno: x for x in data_manager.parsed_cache[p] if x.idInterno}

    soma_doc_map = {x.docSoma: x for x in data_manager.parsed_cache["SOMA"] if x.docSoma}

    # Dicts de atualizações por sheet: sheet_name -> col_idx -> {row_num: new_val}
    updates_by_sheet = defaultdict(lambda: defaultdict(dict))
    relatorio_ajustes = []

    co_rows = data_manager.parsed_cache["CONTAORDEM_RAW"]
    co_cols = MAPA_COLUNAS_SHEETS["CONTAORDEM"]

    # 1. Analisar CONTAORDEM: tipo Entrada/Saída com DOC. SOMA != 7 caracteres
    for co in co_rows:
        if not co.idInterno or not co.processoProx:
            continue
        proc_canon = processoCanonico57(co.processoProx)
        orig = maps.get(proc_canon, {}).get(co.idInterno)
        co_doc = str(co.docSoma or "").strip()

        # Se tipo Entrada/Saída e DOC. SOMA não tem 7 dígitos numéricos
        if tipoFinanceiroGeral57(co.tipo) in ["ENTRADA", "SAÍDA"] and (len(co_doc) != 7 or not co_doc.isdigit()):
            novo_doc = ""
            orig_doc = str(orig.docSoma or "").strip() if orig else ""

            # Caso 1a: Origem tem DOC de 7 dígitos válido
            if orig and len(orig_doc) == 7 and orig_doc.isdigit():
                novo_doc = orig_doc
            # Caso 1b: Buscar no SOMA pelo valor, data e tipo
            else:
                candidatos = [
                    s for s in data_manager.parsed_cache["SOMA"]
                    if len(s.docSoma) == 7 and s.docSoma.isdigit()
                    and eq57(abs57(s.valorRaw), abs57(co.valorRaw), TOL)
                    and tipoFinanceiroGeral57(s.tipo) == tipoFinanceiroGeral57(co.tipo)
                    and s.dataFmt == co.dataFmt
                ]
                if len(candidatos) == 1:
                    novo_doc = candidatos[0].docSoma

            if novo_doc:
                updates_by_sheet["CONTAORDEM"][co_cols["DOC"]][co.linhaFonte] = novo_doc
                relatorio_ajustes.append({
                    "sheet": "CONTAORDEM",
                    "row": co.linhaFonte,
                    "id": co.idInterno,
                    "campo": "DOC. SOMA",
                    "de": co_doc,
                    "para": novo_doc,
                    "motivo": "Correção de DOC pendente para 7 dígitos"
                })
                co.docSoma = novo_doc
                co_doc = novo_doc

                # Atualizar descrição soma se existir no SOMA
                if novo_doc in soma_doc_map and soma_doc_map[novo_doc].desc:
                    nova_desc = soma_doc_map[novo_doc].desc
                    if cmp57(co.descSoma) != cmp57(nova_desc):
                        updates_by_sheet["CONTAORDEM"][co_cols["DESC_SOMA"]][co.linhaFonte] = nova_desc
                        co.descSoma = nova_desc

        # 2. Sincronização Destino ↔ CONTAORDEM
        if len(co_doc) == 7 and co_doc.isdigit() and orig:
            orig_sheet_name = (
                CONFIG_EXT["FINANCEIRO"]["sheetName"] if proc_canon == "FINANCEIRO"
                else (CONFIG_EXT["VC_VENDAS"]["sheetName"] if proc_canon == "VC_VENDAS" else proc_canon)
            )
            orig_cols = MAPA_COLUNAS_SHEETS.get(orig_sheet_name) or MAPA_COLUNAS_SHEETS.get(proc_canon)

            if orig_cols:
                orig_doc = str(orig.docSoma or "").strip()
                if orig_doc != co_doc:
                    updates_by_sheet[orig_sheet_name][orig_cols["DOC"]][orig.linhaFonte] = co_doc
                    relatorio_ajustes.append({
                        "sheet": orig_sheet_name,
                        "row": orig.linhaFonte,
                        "id": orig.idInterno,
                        "campo": "DOC. SOMA",
                        "de": orig_doc,
                        "para": co_doc,
                        "motivo": "Sincronização com DOC. SOMA da CONTAORDEM"
                    })
                    orig.docSoma = co_doc

                orig_desc = str(orig.descSoma or "").strip()
                co_desc = str(co.descSoma or "").strip()
                if co_desc and orig_desc != co_desc:
                    updates_by_sheet[orig_sheet_name][orig_cols["DESC_SOMA"]][orig.linhaFonte] = co_desc
                    relatorio_ajustes.append({
                        "sheet": orig_sheet_name,
                        "row": orig.linhaFonte,
                        "id": orig.idInterno,
                        "campo": "DESCRIÇÃO SOMA",
                        "de": orig_desc,
                        "para": co_desc,
                        "motivo": "Sincronização com DESCRIÇÃO SOMA da CONTAORDEM"
                    })
                    orig.descSoma = co_desc

        # 3. Validar se a descrição da CONTAORDEM diverge do SOMA
        if len(co_doc) == 7 and co_doc.isdigit() and co_doc in soma_doc_map:
            soma_item = soma_doc_map[co_doc]
            if soma_item.desc and cmp57(co.descSoma) != cmp57(soma_item.desc):
                nova_desc = soma_item.desc
                updates_by_sheet["CONTAORDEM"][co_cols["DESC_SOMA"]][co.linhaFonte] = nova_desc
                relatorio_ajustes.append({
                    "sheet": "CONTAORDEM",
                    "row": co.linhaFonte,
                    "id": co.idInterno,
                    "campo": "DESCRIÇÃO SOMA",
                    "de": co.descSoma,
                    "para": nova_desc,
                    "motivo": "Correção para corresponder ao SOMA.DESCRIÇÃO"
                })
                co.descSoma = nova_desc
                if orig:
                    orig_sheet_name = (
                        CONFIG_EXT["FINANCEIRO"]["sheetName"] if proc_canon == "FINANCEIRO"
                        else (CONFIG_EXT["VC_VENDAS"]["sheetName"] if proc_canon == "VC_VENDAS" else proc_canon)
                    )
                    orig_cols = MAPA_COLUNAS_SHEETS.get(orig_sheet_name) or MAPA_COLUNAS_SHEETS.get(proc_canon)
                    if orig_cols:
                        updates_by_sheet[orig_sheet_name][orig_cols["DESC_SOMA"]][orig.linhaFonte] = nova_desc
                        orig.descSoma = nova_desc

    # 4. Resolução de DOC_SOMA_REUTILIZADO_CO por desempate de sequencial SOMA (Nxxx)
    co_por_doc = defaultdict(list)
    for co in co_rows:
        d = str(co.docSoma or "").strip()
        if len(d) == 7 and d.isdigit():
            co_por_doc[d].append(co)

    soma_por_doc = defaultdict(list)
    for s in data_manager.parsed_cache["SOMA"]:
        d = str(s.docSoma or "").strip()
        if len(d) == 7 and d.isdigit():
            soma_por_doc[d].append(s)

    docs_atribuidos_co = {str(x.docSoma or "").strip() for x in co_rows if x.docSoma}

    for doc_repetido, lista_co in list(co_por_doc.items()):
        qtd_soma = len(soma_por_doc.get(doc_repetido, []))
        if len(lista_co) > qtd_soma and qtd_soma > 0:
            item_soma_ref = soma_por_doc[doc_repetido][0]
            data_ref = item_soma_ref.dataFmt
            val_ref = abs57(item_soma_ref.valorRaw)
            tipo_ref = tipoFinanceiroGeral57(item_soma_ref.tipo)

            candidatos_soma = [
                s for s in data_manager.parsed_cache["SOMA"]
                if len(s.docSoma) == 7 and s.docSoma.isdigit()
                and s.docSoma not in docs_atribuidos_co
                and s.dataFmt == data_ref
                and eq57(abs57(s.valorRaw), val_ref, TOL)
                and tipoFinanceiroGeral57(s.tipo) == tipo_ref
            ]

            def extrair_n_val(txt):
                m = re.search(r"\s+N(\d{3})$", str(txt or ""), re.I)
                return int(m.group(1)) if m else 0

            candidatos_soma.sort(key=lambda x: extrair_n_val(x.desc))

            for s_orfao in candidatos_soma:
                if len(lista_co) <= qtd_soma:
                    break

                # Linha de CONTAORDEM a reatribuir
                co_alvo = lista_co[-1]
                novo_doc = s_orfao.docSoma
                nova_desc = s_orfao.desc
                de_doc = co_alvo.docSoma
                de_desc = co_alvo.descSoma

                updates_by_sheet["CONTAORDEM"][co_cols["DOC"]][co_alvo.linhaFonte] = novo_doc
                updates_by_sheet["CONTAORDEM"][co_cols["DESC_SOMA"]][co_alvo.linhaFonte] = nova_desc
                relatorio_ajustes.append({
                    "sheet": "CONTAORDEM",
                    "row": co_alvo.linhaFonte,
                    "id": co_alvo.idInterno,
                    "campo": "DOC. SOMA",
                    "de": de_doc,
                    "para": novo_doc,
                    "motivo": f"Desempate de DOC_SOMA_REUTILIZADO_CO por sequencial SOMA ({nova_desc})"
                })
                relatorio_ajustes.append({
                    "sheet": "CONTAORDEM",
                    "row": co_alvo.linhaFonte,
                    "id": co_alvo.idInterno,
                    "campo": "DESCRIÇÃO SOMA",
                    "de": de_desc,
                    "para": nova_desc,
                    "motivo": "Ajuste do sequencial SOMA correspondente"
                })
                co_alvo.docSoma = novo_doc
                co_alvo.descSoma = nova_desc
                docs_atribuidos_co.add(novo_doc)

                # Replicar no destino (ex: T_EXTRATO)
                proc_canon = processoCanonico57(co_alvo.processoProx)
                orig = maps.get(proc_canon, {}).get(co_alvo.idInterno)
                if orig:
                    orig_sheet_name = (
                        CONFIG_EXT["FINANCEIRO"]["sheetName"] if proc_canon == "FINANCEIRO"
                        else (CONFIG_EXT["VC_VENDAS"]["sheetName"] if proc_canon == "VC_VENDAS" else proc_canon)
                    )
                    orig_cols = MAPA_COLUNAS_SHEETS.get(orig_sheet_name) or MAPA_COLUNAS_SHEETS.get(proc_canon)
                    if orig_cols:
                        updates_by_sheet[orig_sheet_name][orig_cols["DOC"]][orig.linhaFonte] = novo_doc
                        updates_by_sheet[orig_sheet_name][orig_cols["DESC_SOMA"]][orig.linhaFonte] = nova_desc
                        orig.docSoma = novo_doc
                        orig.descSoma = nova_desc

                lista_co.remove(co_alvo)

    # 5. Correção de descrições invertidas na folha SAÍDAS
    if "SAÍDAS" in data_manager.parsed_cache:
        saidas_cols = MAPA_COLUNAS_SHEETS.get("SAÍDAS", {})
        col_desc_compra = saidas_cols.get("DESC_COMPRA", 9)
        for s_item in data_manager.parsed_cache["SAÍDAS"]:
            if s_item.idInterno:
                co_corr = next((x for x in co_rows if x.idInterno == s_item.idInterno), None)
                if co_corr and co_corr.desc and s_item.desc and cmp57(co_corr.desc) != cmp57(s_item.desc):
                    if "SAI0000000001" in s_item.idInterno or "SAI0000000002" in s_item.idInterno:
                        nova_desc_compra = co_corr.desc
                        updates_by_sheet["SAÍDAS"][col_desc_compra][s_item.linhaFonte] = nova_desc_compra
                        relatorio_ajustes.append({
                            "sheet": "SAÍDAS",
                            "row": s_item.linhaFonte,
                            "id": s_item.idInterno,
                            "campo": "DESCRIÇÃO DA COMPRA",
                            "de": s_item.desc,
                            "para": nova_desc_compra,
                            "motivo": "Correção de descrição invertida em SAÍDAS"
                        })
                        s_item.desc = nova_desc_compra

    total_updates = sum(len(row_dict) for cols_dict in updates_by_sheet.values() for row_dict in cols_dict.values())
    logger.info(f"Identificadas {total_updates} atualizações de sincronia em {len(updates_by_sheet)} sheets.")

    if aplicar_planilhas and total_updates > 0:
        logger.info("Gravando atualizações no Google Sheets...")
        for sheet_name, cols_dict in updates_by_sheet.items():
            is_ext = sheet_name in [CONFIG_EXT["FINANCEIRO"]["sheetName"], CONFIG_EXT["VC_VENDAS"]["sheetName"]]
            sh_target = data_manager.sh_ext if is_ext else data_manager.sh_main
            ws = data_manager._get_ws(sh_target, sheet_name)

            for col_idx, row_updates in cols_dict.items():
                if row_updates:
                    col_let = col_letter(col_idx)
                    logger.info(f"Gravando {len(row_updates)} células na sheet '{sheet_name}', coluna {col_let}...")
                    aplicar_atualizacoes_coluna(ws, col_idx, row_updates)
                    time.sleep(1.0)

        logger.info("Gravação de correções concluída! Recarregando dados...")
        data_manager.carregar_tudo()

    return {
        "total_updates": total_updates,
        "detalhes": relatorio_ajustes,
        "por_sheet": {s: sum(len(d) for d in c.values()) for s, c in updates_by_sheet.items()}
    }


# ============================================================================
# MAIN / CLI
# ============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Ronda_Ficheiro - Auditoria Tesouraria SOMA (v57) em Python"
    )
    parser.add_argument("--ano", type=int, default=None, help="Ano para auditar (ex: 2026)")
    parser.add_argument("--mes", type=int, default=None, help="Mês para auditar (1 a 12, ex: 9)")
    parser.add_argument("--desde", type=str, default=None, help="Auditar mês a mês desde AAAA-MM (ex: 2024-01)")
    parser.add_argument("--ate", type=str, default="2026-09", help="Auditar até AAAA-MM (padrão: 2026-09)")
    parser.add_argument("--tudo", action="store_true", help="Audita todo o período de 2024-01 até 2026-09 mês a mês")
    parser.add_argument("--gravar-sheet", action="store_true", help="Grava o balancete e detalhe na sheet T_AUDIT")
    parser.add_argument("--exportar-json", type=str, default=None, help="Arquivo JSON para exportar resumo de erros")
    parser.add_argument("--auto-corrigir", action="store_true", help="Corrige automaticamente DOC. SOMA e Descrição Soma nas planilhas antes do relatório")
    parser.add_argument("--apenas-corrigir", action="store_true", help="Apenas executa as correções sem gerar relatório completo")
    parser.add_argument("--credentials", type=str, default=None, help="Caminho para o JSON de credenciais")
    parser.add_argument("--spreadsheet-url", type=str, default=None, help="URL da planilha principal")
    return parser.parse_args()


def main():
    args = parse_args()

    creds_path = (
        args.credentials or
        os.getenv("GOOGLE_CREDENTIALS_PATH") or
        "C:/workspace/Tesouraria-SOMA/credentials/sheets-service-account.json"
    )
    spread_url = (
        args.spreadsheet_url or
        os.getenv("SPREADSHEET_URL") or
        "https://docs.google.com/spreadsheets/d/1poVWJGSBb13_2S1YKEzvFmkB9Ru0ZVzfQ0OEcMkfOZw/edit"
    )

    if not os.path.isabs(creds_path):
        creds_path = str((BASE_DIR / creds_path).resolve())

    if not os.path.exists(creds_path):
        logger.error(f"Arquivo de credenciais não encontrado: {creds_path}")
        sys.exit(1)

    logger.info("Conectando ao Google Sheets via Service Account...")
    gc = gspread.service_account(filename=creds_path)
    data_manager = SheetsDataManager(gc, spread_url)
    data_manager.carregar_tudo()

    # AUTO-CORREÇÃO (se solicitado via --auto-corrigir ou --apenas-corrigir)
    if args.auto_corrigir or args.apenas_corrigir:
        resultado_correcao = executar_autocorrecao_sincronia(
            data_manager=data_manager,
            aplicar_planilhas=True
        )
        print("\n" + "=" * 80)
        print("🛠️ RELATÓRIO DE AUTO-CORREÇÃO E SINCRONIZAÇÃO")
        print("=" * 80)
        print(f"Total de atualizações aplicadas: {resultado_correcao['total_updates']}")
        for s_name, qtd in resultado_correcao["por_sheet"].items():
            print(f"  - Sheet '{s_name}': {qtd} células ajustadas")
        if resultado_correcao["detalhes"]:
            print("\nAmostra de correções efetuadas:")
            for d in resultado_correcao["detalhes"][:15]:
                print(f"  [{d['sheet']}] Linha {d['row']} (ID: {d['id']}): {d['campo']} -> De: {d['de']!r} | Para: {d['para']!r} ({d['motivo']})")
        print("=" * 80 + "\n")

        if args.apenas_corrigir:
            return

    # MODO 1: Intervalo mês a mês (desde 2024-01 ou --tudo)
    if args.tudo or args.desde:
        desde_str = args.desde or "2024-01"
        ate_str = args.ate or "2026-09"
        ano_ini, mes_ini = map(int, desde_str.split("-"))
        ano_fim, mes_fim = map(int, ate_str.split("-"))

        resultados = executar_auditoria_intervalo(
            data_manager=data_manager,
            ano_inicio=ano_ini,
            mes_inicio=mes_ini,
            ano_fim=ano_fim,
            mes_fim=mes_fim,
            exportar_json=args.exportar_json
        )

        # Imprimir tabela resumo consolidada
        print("\n" + "=" * 95)
        print(f"📊 CONSOLIDADO AUDITORIA MÊS A MÊS ({desde_str} até {ate_str})")
        print("=" * 95)
        print(f"{'Mês/Ano':<12} {'✅ OK':>8} {'🟡 Pend':>8} {'❌ Err':>8} {'Cob.Falhas':>12} {'Dif.Entradas':>16} {'Dif.Saídas':>16} {'Status'}")
        print("-" * 95)
        for r in resultados:
            b = r.balancete
            dif_e = b.entrada.diffSomaCO if b else 0.0
            dif_s = b.saida.diffSomaCO if b else 0.0
            is_ok = (r.erro == 0 and r.falhasCobertura == 0 and abs(dif_e) <= TOL and abs(dif_s) <= TOL)
            st_text = "✅ CONCILIADO" if is_ok else "⚠️ DIVERGÊNCIA"
            print(f"{r.periodo.mesTexto[:3]+'/'+str(r.periodo.ano):<12} {r.ok:>8} {r.amarelo:>8} {r.erro:>8} {r.falhasCobertura:>12} {fmt57(dif_e)+' €':>16} {fmt57(dif_s)+' €':>16} {st_text}")
        print("=" * 95 + "\n")
        return

    # MODO 2: Mês específico
    if args.ano and args.mes:
        periodo = criar_periodo(args.ano, args.mes)
    else:
        # Padrão: ler de T_AUDIT!A1:B1 ou setembro de 2026
        try:
            periodo = data_manager.obter_periodo_planilha()
        except Exception:
            periodo = criar_periodo(2026, 9)

    ctx = ContextoAuditoria(periodo=periodo, data_manager=data_manager, tol=TOL)
    resumo = auditarMes57(ctx)
    imprimir_resumo_execucao(resumo)

    if args.gravar_sheet:
        gravarAuditSheet57(gc, spread_url, resumo)

    if args.exportar_json:
        exportar_relatorio_json([resumo], args.exportar_json)


if __name__ == "__main__":
    main()

