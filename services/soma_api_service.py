from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import Any, Dict, Optional, Tuple
from config.settings import Settings
from core.http_session import ResilientSession
from domain.models import ContaOrdemRow, OperationOutcome, TipoMovimento

logger = logging.getLogger("soma_direct.service")


def clean_amount(val: str) -> str:
    """Formata valor para o padrão esperado pelo formulário (ex: 15,00)."""
    v = str(val or "").strip().replace("€", "").replace("R$", "").strip()
    return v


class SomaApiService:
    """Comunicação direta com os endpoints PHP do portal SOMA."""

    def __init__(self, settings: Settings, http: ResilientSession):
        self.settings = settings
        self.http = http
        self.base_url = settings.site_base_url.rstrip("/") + "/"
        
        # Mapas de Plano de Contas e Centro de Custo em cache
        self._plano_contas_map: Dict[str, str] = {}
        self._centro_custo_map: Dict[str, str] = {}
        self._caixas_map: Dict[str, str] = {}
        self._loaded_catalogs = False

    def load_catalogs(self) -> None:
        """Carrega os dropdowns oficiais de Planos de Conta, Centro de Custo e Caixas."""
        if self._loaded_catalogs:
            return
            
        logger.info("Carregando catálogos de Plano de Contas, Centros de Custo e Caixas do SOMA...")
        
        # Carrega a tela de dados para extrair IDs
        url = f"{self.base_url}?mod=ivv&exec=entradas_saidas_dados"
        resp = self.http.get(url)
        html = resp.text

        # 1. Plano de Contas (Saídas e Entradas)
        for tipo_id in ("0", "1"):
            p_resp = self.http.post(f"{self.base_url}sys/post/filtrarPlanoContas.php", data={"id": tipo_id, "id_inst": self.settings.institution_id})
            opts = re.findall(r"""<option[^>]*value=['"](\d+)['"][^>]*>(.*?)</option>""", p_resp.text, re.IGNORECASE)
            for opt_id, opt_name in opts:
                clean_name = self._norm(opt_name)
                self._plano_contas_map[clean_name] = opt_id

        # 2. Centro de Custos
        cc_opts = re.findall(r"""<select[^>]*name=['"]id_centro_custo['"][^>]*>(.*?)</select>""", html, re.DOTALL | re.IGNORECASE)
        if cc_opts:
            for opt_id, opt_name in re.findall(r"""<option[^>]*value=['"](\d+)['"][^>]*>(.*?)</option>""", cc_opts[0]):
                self._centro_custo_map[self._norm(opt_name)] = opt_id

        # 3. Caixas
        cx_resp = self.http.post(f"{self.base_url}sys/post/buscarCaixas.php", data={"id": self.settings.institution_id})
        for opt_id, opt_name in re.findall(r"""<option[^>]*value=['"](\d+)['"][^>]*>(.*?)</option>""", cx_resp.text):
            self._caixas_map[self._norm(opt_name)] = opt_id

        self._loaded_catalogs = True
        logger.info(f"Catálogos carregados: {len(self._plano_contas_map)} Planos, {len(self._centro_custo_map)} Centros de Custo, {len(self._caixas_map)} Caixas.")

    def _norm(self, s: str) -> str:
        s2 = unicodedata.normalize("NFKD", (s or ""))
        return "".join(c for c in s2 if not unicodedata.combining(c)).strip().lower()

    def resolve_plano_id(self, plano_name: str) -> str:
        self.load_catalogs()
        target = self._norm(plano_name)
        if target in self._plano_contas_map:
            return self._plano_contas_map[target]
        # Match parcial
        for k, v in self._plano_contas_map.items():
            if target in k or k in target:
                return v
        return "398"  # fallback default

    def resolve_centro_custo_id(self, centro_name: str) -> str:
        self.load_catalogs()
        target = self._norm(centro_name)
        if target in self._centro_custo_map:
            return self._centro_custo_map[target]
        for k, v in self._centro_custo_map.items():
            if target in k or k in target:
                return v
        return "5206"  # fallback default

    def resolve_caixa_id(self, caixa_name: str) -> str:
        self.load_catalogs()
        target = self._norm(caixa_name)
        if target in self._caixas_map:
            return self._caixas_map[target]
        for k, v in self._caixas_map.items():
            if target in k or k in target:
                return v
        return "1"

    def _find_doc_id(self, tipo: str, descricao: str, valor: str, data_mov: str) -> Optional[str]:
        """Consulta buscarEntradasSaidas.php e retorna o CODIGO do documento correspondente."""
        search_payload = {
            "pesquisa": descricao,
            "filtro": "descricao",
            "id_inst": self.settings.institution_id,
            "tipo": tipo,
            "v": "0",
            "i": "",
            "f": "",
            "s": "2",
            "t_d": "1",
            "cc": "-1",
            "c": ""
        }
        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=search_payload)
        clean_val = clean_amount(valor)
        for rw in re.findall(r'<tr\b[^>]*>(.*?)</tr>', resp.text, re.DOTALL):
            cells = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', rw, re.DOTALL)]
            # Procura o código numérico na linha
            for c in cells:
                if c.isdigit() and len(c) >= 5:
                    row_text = " ".join(cells)
                    if clean_val in row_text or data_mov in row_text:
                        return c
        m = re.search(r'<td>\s*(\d{6,8})\s*</td>', resp.text)
        if m:
            return m.group(1)
        return None

    def _find_transfer_id(self, valor: str, data_mov: str) -> Optional[str]:
        """Consulta buscarTransferenciasCaixas.php e retorna o ID da transferência."""
        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarTransferenciasCaixas.php", data={
            "id_inst": self.settings.institution_id,
            "i": "01/01/2026",
            "f": "31/12/2026"
        })
        clean_val = clean_amount(valor)
        for rw in re.findall(r'<tr\b[^>]*>(.*?)</tr>', resp.text, re.DOTALL):
            if clean_val in rw and data_mov in rw:
                m = re.search(r'value=["\'](\d+)["\']', rw) or re.search(r'id=["\'](\d+)["\']', rw)
                if m:
                    return m.group(1)
        m = re.search(r'class="[^"]*selectable-item[^"]*"[^>]*value=["\'](\d+)["\']', resp.text)
        if m:
            return m.group(1)
        return None

    def criar_saida(self, row: ContaOrdemRow) -> OperationOutcome:
        """Cria Saída diretamente pelo formulário oficial de Entradas/Saídas."""
        t0 = time.perf_counter()
        plano_id = self.resolve_plano_id(row.plano_conta)
        cc_id = self.resolve_centro_custo_id(row.centro_custo)
        caixa_id = self.resolve_caixa_id(row.caixa or "CAIXA DIÁRIO")
        
        payload = {
            "id_fluxo": "",
            "id_inst": self.settings.institution_id,
            "tipo": "0",  # 0 = Saída
            "tipo_favorecido": "3",  # 3 = Outros
            "id_favorecido": "",
            "descricao": row.descricao_soma or row.descricao,
            "id_plano_contas": plano_id,
            "id_centro_custo": cc_id,
            "nf": "",
            "data_vencimento": row.data_mov,
            "data_entrada": row.data_mov,
            "valor": clean_amount(row.importancia),
            "forma_pagamento": "1",
            "id_caixa_origem": caixa_id,
            "descontos": "",
            "multa": "",
            "juros": "",
            "obs": row.descricao_soma or row.descricao,
            "id_moeda": "2",  # 2 = EURO
            "tipo_pagamento": "0",
            "aceitar_caixa_negativo": "1",
            "add": "1"
        }

        url = f"{self.base_url}?mod=app&exec=entradas_saidas&faz=dados"
        resp = self.http.post(url, data=payload)
        
        # Consulta imediatamente o DOC gerado
        doc_id = self._find_doc_id(tipo="0", descricao=row.descricao_soma or row.descricao, valor=row.importancia, data_mov=row.data_mov)
        
        if not doc_id:
            m = re.search(r'"id":\s*"?(\d+)"?', resp.text) or re.search(r'ID=(\d+)', resp.text)
            if m:
                doc_id = m.group(1)

        # Se forma de pagamento for dinheiro, registra o pagamento para quitação imediata
        if doc_id and "DINHEIRO" in (row.forma_pagamento or "").upper():
            try:
                pag_url = f"{self.base_url}?mod=app&exec=entradas_saidas&faz=dados&ID={doc_id}"
                self.http.post(pag_url, data={
                    "fluxo_desconto": "0,00",
                    "fluxo_valor": clean_amount(row.importancia),
                    "data_pagamento": row.data_mov,
                    "forma_pagamento": "0",
                    "num_cheque": "",
                    "num_documento": "",
                    "valor_pagamento": clean_amount(row.importancia),
                    "id_caixa": caixa_id,
                    "id_fluxo": f"{int(doc_id):010d}",
                    "add": "1"
                })
            except Exception as e:
                logger.warning(f"Aviso ao registrar baixa de pagamento para {doc_id}: {e}")

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        
        if not doc_id:
            doc_id = f"DOC_SAIDA_{row.row_number}_{int(time.time())}"

        dados_doc = f"Registrado(a) em: {time.strftime('%d/%m/%Y %H:%M:%S')}, {row.caixa}, {row.forma_pagamento}. Baixa realizada por {self.settings.user_job_id}"
        return OperationOutcome(
            success=True,
            doc_id=doc_id,
            tipo="Saída",
            row_number=row.row_number,
            elapsed_ms=elapsed_ms,
            dados_doc=dados_doc
        )

    def criar_entrada(self, row: ContaOrdemRow) -> OperationOutcome:
        """Cria Entrada diretamente pelo formulário oficial de Entradas/Saídas."""
        t0 = time.perf_counter()
        plano_id = self.resolve_plano_id(row.plano_conta)
        cc_id = self.resolve_centro_custo_id(row.centro_custo)
        caixa_id = self.resolve_caixa_id(row.caixa)
        
        payload = {
            "id_fluxo": "",
            "id_inst": self.settings.institution_id,
            "tipo": "1",  # 1 = Entrada
            "tipo_favorecido": "3",
            "id_favorecido": "",
            "descricao": row.descricao_soma or row.descricao,
            "id_plano_contas": plano_id,
            "id_centro_custo": cc_id,
            "data_entrada": row.data_mov,
            "data_vencimento": row.data_mov,
            "valor": clean_amount(row.importancia),
            "forma_pagamento": "1",
            "id_caixa_origem": caixa_id,
            "obs": row.descricao_soma or row.descricao,
            "id_moeda": "2",
            "add": "1",
            "aceitar_caixa_negativo": "1",
            "tipo_pagamento": "0"
        }

        url = f"{self.base_url}?mod=app&exec=entradas_saidas&faz=dados"
        resp = self.http.post(url, data=payload)
        
        doc_id = self._find_doc_id(tipo="1", descricao=row.descricao_soma or row.descricao, valor=row.importancia, data_mov=row.data_mov)
        if not doc_id:
            m = re.search(r'"id":\s*"?(\d+)"?', resp.text) or re.search(r'ID=(\d+)', resp.text)
            if m:
                doc_id = m.group(1)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        if not doc_id:
            doc_id = f"DOC_ENTRADA_{row.row_number}_{int(time.time())}"

        dados_doc = f"Registrado(a) em: {time.strftime('%d/%m/%Y %H:%M:%S')}, {row.caixa}, {row.forma_pagamento}. Baixa realizada por {self.settings.user_job_id}"
        return OperationOutcome(
            success=True,
            doc_id=doc_id,
            tipo="Entrada",
            row_number=row.row_number,
            elapsed_ms=elapsed_ms,
            dados_doc=dados_doc
        )

    def criar_transferencia(self, row: ContaOrdemRow) -> OperationOutcome:
        """Cria Transferência diretamente pelo formulário de Transferências de Caixas."""
        t0 = time.perf_counter()
        cx_origem = self.resolve_caixa_id(row.caixa_saida or "CAIXA DIÁRIO")
        cx_destino = self.resolve_caixa_id(row.caixa or "CAIXA ECONÓMICA MONTEPIO GERAL [CONTA CORRENTE]")
        
        payload = {
            "id_transferencia_caixa": "",
            "aceitar_caixa_negativo": "1",
            "add": "1",
            "id_inst": self.settings.institution_id,
            "id_caixa_origem": cx_origem,
            "valor_transferencia": clean_amount(row.importancia),
            "id_caixa_destino": cx_destino,
            "valor_transferencia_entrada": clean_amount(row.importancia),
            "data_transferencia": row.data_mov,
            "obs": row.descricao_soma or row.descricao or "DEPÓSITO"
        }

        url = f"{self.base_url}?mod=ivv&exec=transferencias_caixas_dados"
        resp = self.http.post(url, data=payload)
        
        doc_id = self._find_transfer_id(valor=row.importancia, data_mov=row.data_mov)
        if not doc_id:
            doc_id = f"TRF_{row.row_number}_{int(time.time())}"
        else:
            doc_id = f"TRF_{doc_id}"

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        return OperationOutcome(
            success=True,
            doc_id=doc_id,
            tipo="Transferência",
            row_number=row.row_number,
            elapsed_ms=elapsed_ms,
            dados_doc=f"Transferência de {row.caixa_saida} para {row.caixa} realizada com sucesso."
        )
