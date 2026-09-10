from __future__ import annotations

import logging
import re
import time
import unicodedata
from html import unescape
from typing import Any, Dict, Optional, Tuple
from config.settings import Settings
from core.http_session import ResilientSession
from domain.models import ContaOrdemRow, OperationOutcome, TipoMovimento, norm_basic

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
        self._confirmation_attempts = 5

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

        # 2. Centro de Custos. O formulário inicial contém apenas "PADRÃO";
        # o JavaScript entradas_saidas_dados_v2.js carrega as opções via AJAX.
        cc_resp = self.http.post(
            f"{self.base_url}sys/post/buscarCentrodeCustoSelect.php",
            data={"id": self.settings.institution_id},
        )
        if not 200 <= cc_resp.status_code < 300:
            raise RuntimeError(f"Falha ao carregar centros de custo: HTTP {cc_resp.status_code}")
        for opt_id, opt_name in re.findall(
            r"""<option[^>]*value=['"](\d+)['"][^>]*>(.*?)</option>""",
            cc_resp.text,
            re.DOTALL | re.IGNORECASE,
        ):
            clean_name = unescape(re.sub(r"<[^>]+>", " ", opt_name))
            self._centro_custo_map[self._norm(clean_name)] = opt_id
        if not self._centro_custo_map:
            raise RuntimeError("O SOMA devolveu um catálogo vazio de centros de custo")

        # 3. Caixas
        cx_resp = self.http.post(f"{self.base_url}sys/post/buscarCaixas.php", data={"id": self.settings.institution_id})
        for opt_id, opt_name in re.findall(r"""<option[^>]*value=['"](\d+)['"][^>]*>(.*?)</option>""", cx_resp.text):
            self._caixas_map[self._norm(opt_name)] = opt_id

        self._loaded_catalogs = True
        logger.info(f"Catálogos carregados: {len(self._plano_contas_map)} Planos, {len(self._centro_custo_map)} Centros de Custo, {len(self._caixas_map)} Caixas.")

    def _norm(self, s: str) -> str:
        s2 = unicodedata.normalize("NFKD", (s or ""))
        return "".join(c for c in s2 if not unicodedata.combining(c)).strip().lower()

    @staticmethod
    def _http_error(resp: Any) -> Optional[str]:
        if not 200 <= resp.status_code < 300:
            return f"HTTP {resp.status_code} devolvido pelo SOMA"
        body = norm_basic(resp.text)
        error_markers = ("erro", "falha", "não foi possível", "nao foi possivel", "acesso negado")
        if any(marker in body for marker in error_markers):
            return "O SOMA devolveu uma mensagem de erro ao gravar o documento"
        return None

    def resolve_plano_id(self, plano_name: str) -> str:
        self.load_catalogs()
        target = self._norm(plano_name)
        if target in self._plano_contas_map:
            return self._plano_contas_map[target]
        # Match parcial
        for k, v in self._plano_contas_map.items():
            if target in k or k in target:
                return v
        raise ValueError(f"Plano de conta não encontrado no SOMA: '{plano_name}'")

    def resolve_centro_custo_id(self, centro_name: str) -> str:
        self.load_catalogs()
        target = self._norm(centro_name)
        if target in self._centro_custo_map:
            return self._centro_custo_map[target]
        for k, v in self._centro_custo_map.items():
            if target in k or k in target:
                return v
        raise ValueError(f"Centro de custo não encontrado no SOMA: '{centro_name}'")

    def resolve_caixa_id(self, caixa_name: str) -> str:
        self.load_catalogs()
        target = self._norm(caixa_name)
        if target in self._caixas_map:
            return self._caixas_map[target]
        for k, v in self._caixas_map.items():
            if target in k or k in target:
                return v
        raise ValueError(f"Caixa não encontrado no SOMA: '{caixa_name}'")

    def _find_doc_id(self, tipo: str, descricao: str, valor: str, data_mov: str) -> Optional[str]:
        """Consulta buscarEntradasSaidas.php e retorna o CODIGO do documento estritamente correspondente."""
        search_payload = {
            "pesquisa": descricao,
            "filtro": "descricao",
            "id_inst": self.settings.institution_id,
            "tipo": tipo,
            "v": "1" if data_mov else "0",
            "i": data_mov or "",
            "f": data_mov or "",
            "s": "2",
            "t_d": "1",
            "cc": "-1",
            "c": "",
        }
        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=search_payload)
        clean_val = clean_amount(valor)
        target_desc = norm_basic(descricao)
        matches = []
        for rw in re.findall(r'<tr\b[^>]*>(.*?)</tr>', resp.text, re.DOTALL):
            cells = [unescape(re.sub(r'<[^>]+>', ' ', c)).strip() for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', rw, re.DOTALL)]
            row_text = " ".join(cells)
            if clean_val not in row_text or (data_mov and data_mov not in row_text):
                continue
            if target_desc and not any(norm_basic(cell) == target_desc for cell in cells):
                continue
            for c in cells:
                if c.isdigit() and len(c) >= 5:
                    matches.append(c)
                    break
        unique = list(dict.fromkeys(matches))
        if len(unique) == 1:
            return unique[0]
        if len(unique) > 1:
            logger.error("Busca ambígua no SOMA: %d documentos correspondem aos mesmos campos.", len(unique))
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

    def _wait_find_doc(self, tipo: str, descricao: str, valor: str, data_mov: str) -> Optional[str]:
        """Aguarda a consistência do índice de pesquisa após a criação."""
        for attempt in range(self._confirmation_attempts):
            doc_id = self._find_doc_id(tipo, descricao, valor, data_mov)
            if doc_id:
                return doc_id
            if attempt < self._confirmation_attempts - 1:
                time.sleep(1)
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
        http_error = self._http_error(resp)
        if http_error:
            return OperationOutcome(False, "", "Saída", row.row_number, int((time.perf_counter() - t0) * 1000), error_message=http_error)

        # Consulta imediatamente o DOC gerado
        doc_id = self._wait_find_doc(tipo="0", descricao=row.descricao_soma or row.descricao, valor=row.importancia, data_mov=row.data_mov)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not doc_id:
            return OperationOutcome(False, "", "Saída", row.row_number, elapsed_ms, error_message="POST concluído, mas o documento não foi confirmado de forma inequívoca no SOMA")

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
        http_error = self._http_error(resp)
        if http_error:
            return OperationOutcome(False, "", "Entrada", row.row_number, int((time.perf_counter() - t0) * 1000), error_message=http_error)

        doc_id = self._wait_find_doc(tipo="1", descricao=row.descricao_soma or row.descricao, valor=row.importancia, data_mov=row.data_mov)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        if not doc_id:
            return OperationOutcome(False, "", "Entrada", row.row_number, elapsed_ms, error_message="POST concluído, mas o documento não foi confirmado de forma inequívoca no SOMA")

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
        http_error = self._http_error(resp)
        if http_error:
            return OperationOutcome(False, "", "Transferência", row.row_number, int((time.perf_counter() - t0) * 1000), error_message=http_error)

        doc_id = self._find_transfer_id(valor=row.importancia, data_mov=row.data_mov)
        if not doc_id:
            return OperationOutcome(False, "", "Transferência", row.row_number, int((time.perf_counter() - t0) * 1000), error_message="Transferência não confirmada no SOMA")
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
