from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
from config.settings import Settings
from core.http_session import ResilientSession
from domain.models import (
    ContaOrdemRow,
    OperationOutcome,
    SomaTransfer,
    TRANSFER_DOC_MARKER,
    TipoMovimento,
    clean_amount_for_comparison,
    norm_basic,
    normalize_date_str,
    parse_transfer_table,
    transfer_key,
)

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
        self._formas_pagamento_map: Dict[str, str] = {}
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

        # 4. Formas de pagamento (mesmo pedido AJAX do formulário Entradas/Saídas)
        fp_resp = self.http.post(f"{self.base_url}sys/post/buscarFormaPagamento.php", data={"id": self.settings.institution_id})
        for opt_id, opt_name in re.findall(r"""<option[^>]*value=['"](\d+)['"][^>]*>(.*?)</option>""", fp_resp.text, re.DOTALL | re.IGNORECASE):
            self._formas_pagamento_map[self._norm(unescape(re.sub(r"<[^>]+>", " ", opt_name)))] = opt_id

        self._loaded_catalogs = True
        logger.info(f"Catálogos carregados: {len(self._plano_contas_map)} Planos, {len(self._centro_custo_map)} Centros de Custo, {len(self._caixas_map)} Caixas.")

    def _norm(self, s: str) -> str:
        s2 = unicodedata.normalize("NFKD", (s or ""))
        return "".join(c for c in s2 if not unicodedata.combining(c)).strip().lower()

    # Palavras inteiras apenas: "erro" não pode casar com "error"/"onerror"
    # presentes no HTML/JS de qualquer página devolvida pelo SOMA.
    _ERROR_MARKERS = re.compile(
        r"\b(?:erro|falha|nao foi possivel|acesso negado)\b"
    )

    @classmethod
    def _http_error(cls, resp: Any) -> Optional[str]:
        if not 200 <= resp.status_code < 300:
            return f"HTTP {resp.status_code} devolvido pelo SOMA"
        body = norm_basic(re.sub(r"<script\b.*?</script>", " ", resp.text or "", flags=re.S | re.I))
        if cls._ERROR_MARKERS.search(body):
            return "O SOMA devolveu uma mensagem de erro ao gravar o documento"
        return None

    @staticmethod
    def _describe_response(resp: Any) -> str:
        """Resumo não sensível da resposta HTTP para diagnóstico em log."""
        text = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", " ", resp.text or "", flags=re.S | re.I)
        text = " ".join(unescape(re.sub(r"<[^>]+>", " ", text)).split())
        final_url = str(getattr(resp, "url", "") or "").split("?", 1)[0]
        return f"HTTP {resp.status_code} url={final_url} corpo='{text[:300]}'"

    # Endpoints de gravação usados pelo JavaScript oficial do SOMA
    # (themes/js/entradas_saidas_dados_v2.js e transferencias_caixas_dados.js).
    ENTRADAS_SAIDAS_ENDPOINT = "sys/app/entradas_saidas.php"
    TRANSFERENCIAS_ENDPOINT = "sys/app/transferencias_caixas.php"

    TRANSFER_STATUS_MESSAGES = {
        2: "SOMA: nenhuma operação foi realizada",
        5: "SOMA: o caixa não possui o valor solicitado para a transferência",
        6: "SOMA: não é possível transferir para um mês fechado",
        7: "SOMA: a sessão expirou",
        11: "SOMA: a transferência tornaria o caixa negativo",
    }

    def _submit_app(self, endpoint: str, payload: Dict[str, str]) -> Tuple[Any, Optional[Dict[str, Any]]]:
        """POST AJAX para sys/app/*.php e interpretação da resposta JSON {status, ...}."""
        resp = self.http.post_ajax(f"{self.base_url}{endpoint}", data=payload)
        data: Optional[Dict[str, Any]] = None
        match = re.search(r"\{.*\}", resp.text or "", re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    data = parsed
            except ValueError:
                data = None
        return resp, data

    @staticmethod
    def _json_status(data: Optional[Dict[str, Any]]) -> Optional[int]:
        try:
            return int((data or {}).get("status"))
        except (TypeError, ValueError):
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

    def resolve_forma_pagamento_id(self, forma_name: str) -> str:
        self.load_catalogs()
        target = self._norm(forma_name)
        if target in self._formas_pagamento_map:
            return self._formas_pagamento_map[target]
        for k, v in self._formas_pagamento_map.items():
            if target and (target in k or k in target):
                return v
        raise ValueError(f"Forma de pagamento não encontrada no SOMA: '{forma_name}'")

    def resolve_caixa_id(self, caixa_name: str) -> str:
        self.load_catalogs()
        # "CAIXA ECONÔMICA MONTEPIO GERAL [CONTA CORRENTE]" -> nome do catálogo
        target = self._norm(re.sub(r"\[.*?\]", "", caixa_name or ""))
        if target in self._caixas_map:
            return self._caixas_map[target]
        for k, v in self._caixas_map.items():
            if target in k or k in target:
                return v
        raise ValueError(f"Caixa não encontrado no SOMA: '{caixa_name}'")

    def buscar_resumo_caixas(self) -> Dict[str, str]:
        """Lê os saldos atuais de Caixas/Bancos via o mesmo endpoint que o dashboard
        oficial usa (sys/post/buscarResumoCaixasNew.php, chamado por themes/js/caixas.js)."""
        resp = self.http.post_ajax(
            f"{self.base_url}sys/post/buscarResumoCaixasNew.php",
            data={"id": self.settings.institution_id},
        )
        http_error = self._http_error(resp)
        if http_error:
            raise RuntimeError(f"Falha ao ler saldos de Caixas/Bancos: {http_error}")

        saldos: Dict[str, str] = {}
        for m in re.finditer(
            r"counter-number-related[^>]*>([^<]*)<.*?counter-label[^>]*>\s*([^<]+?)\s*</div>",
            resp.text,
            re.DOTALL,
        ):
            valor = unescape(m.group(1)).strip()
            label = unescape(re.sub(r"\s+", " ", m.group(2))).strip()
            if label:
                saldos[label] = valor

        if not saldos:
            raise RuntimeError("O SOMA devolveu um resumo de Caixas/Bancos vazio")
        return saldos

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

    def list_transfers_on(self, data_mov: str) -> List[SomaTransfer]:
        """Lista as transferências de caixas do SOMA numa única data."""
        d_norm = normalize_date_str(data_mov)
        if not d_norm:
            return []
        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarTransferenciasCaixas.php", data={
            "id_inst": self.settings.institution_id,
            "i": d_norm,
            "f": d_norm,
        })
        if not 200 <= resp.status_code < 300:
            raise RuntimeError(f"Falha ao pesquisar transferências no SOMA: HTTP {resp.status_code}")
        return [t for t in parse_transfer_table(resp.text) if normalize_date_str(t.data) == d_norm]

    def find_transfers(self, row: ContaOrdemRow) -> Tuple[List[SomaTransfer], List[SomaTransfer]]:
        """Devolve (mesma data+valor, mesma data+valor+caixas) para a linha.

        O primeiro conjunto serve de guarda anti-duplicado: qualquer transferência
        com a mesma data e valor bloqueia uma criação automática.
        """
        valor = clean_amount_for_comparison(row.importancia)
        same_amount = [
            t for t in self.list_transfers_on(row.data_mov)
            if clean_amount_for_comparison(t.valor_saida) == valor
        ]
        key = transfer_key(row.data_mov, row.importancia, row.caixa_saida, row.caixa)
        exact = [
            t for t in same_amount
            if transfer_key(t.data, t.valor_saida, t.caixa_origem, t.caixa_destino) == key
        ]
        return same_amount, exact

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
            "forma_pagamento": self.resolve_forma_pagamento_id(row.forma_pagamento),
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

        resp, data = self._submit_app(self.ENTRADAS_SAIDAS_ENDPOINT, payload)
        status = self._json_status(data)
        if status != 1:
            message = f"SOMA recusou a gravação da Saída (status={status})" if status is not None else (self._http_error(resp) or "Resposta inválida do SOMA ao gravar")
            logger.error("Saída linha %s: %s | resposta=%s | %s", row.row_number, message, data, self._describe_response(resp))
            return OperationOutcome(False, "", "Saída", row.row_number, int((time.perf_counter() - t0) * 1000), error_message=message)

        # O SOMA devolve o ID do documento (usado no link ?ID=); confirma pela pesquisa se faltar.
        doc_id = str((data or {}).get("id") or "").strip()
        if not re.fullmatch(r"\d{7}", doc_id):
            doc_id = self._wait_find_doc(tipo="0", descricao=row.descricao_soma or row.descricao, valor=row.importancia, data_mov=row.data_mov) or ""

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not doc_id:
            logger.error("Saída linha %s gravada (status 1) mas sem DOC identificado: resposta=%s", row.row_number, data)
            return OperationOutcome(False, "", "Saída", row.row_number, elapsed_ms, error_message="SOMA gravou o documento, mas o DOC não foi identificado de forma inequívoca")

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
            "forma_pagamento": self.resolve_forma_pagamento_id(row.forma_pagamento),
            "id_caixa_origem": caixa_id,
            "obs": row.descricao_soma or row.descricao,
            "id_moeda": "2",
            "add": "1",
            "aceitar_caixa_negativo": "1",
            "tipo_pagamento": "0"
        }

        resp, data = self._submit_app(self.ENTRADAS_SAIDAS_ENDPOINT, payload)
        status = self._json_status(data)
        if status != 1:
            message = f"SOMA recusou a gravação da Entrada (status={status})" if status is not None else (self._http_error(resp) or "Resposta inválida do SOMA ao gravar")
            logger.error("Entrada linha %s: %s | resposta=%s | %s", row.row_number, message, data, self._describe_response(resp))
            return OperationOutcome(False, "", "Entrada", row.row_number, int((time.perf_counter() - t0) * 1000), error_message=message)

        # O SOMA devolve o ID do documento (usado no link ?ID=); confirma pela pesquisa se faltar.
        doc_id = str((data or {}).get("id") or "").strip()
        if not re.fullmatch(r"\d{7}", doc_id):
            doc_id = self._wait_find_doc(tipo="1", descricao=row.descricao_soma or row.descricao, valor=row.importancia, data_mov=row.data_mov) or ""

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not doc_id:
            logger.error("Entrada linha %s gravada (status 1) mas sem DOC identificado: resposta=%s", row.row_number, data)
            return OperationOutcome(False, "", "Entrada", row.row_number, elapsed_ms, error_message="SOMA gravou o documento, mas o DOC não foi identificado de forma inequívoca")

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
        """Cria Transferência como o formulário oficial (transferencias_caixas_dados.js).

        Campos iguais ao preenchimento Selenium do projeto SOMA: caixa saída/entrada,
        centro de custo saída/entrada (CONTAORDEM[CENTRO DE CUSTO] ou PADRÃO), valor,
        data e descrição.
        """
        t0 = time.perf_counter()
        cx_origem = self.resolve_caixa_id(row.caixa_saida or "CAIXA DIÁRIO")
        cx_destino = self.resolve_caixa_id(row.caixa or "CAIXA ECONÓMICA MONTEPIO GERAL [CONTA CORRENTE]")
        cc_id = self.resolve_centro_custo_id((row.centro_custo or "PADRÃO").strip() or "PADRÃO")

        payload = {
            "id_transferencia_caixa": "",
            "aceitar_caixa_negativo": "1",
            "add": "1",
            "id_inst": self.settings.institution_id,
            "id_caixa_origem": cx_origem,
            "id_cc_saida": cc_id,
            "valor_transferencia": clean_amount(row.importancia),
            "id_caixa_destino": cx_destino,
            "id_cc_entrada": cc_id,
            "valor_transferencia_entrada": clean_amount(row.importancia),
            "data_transferencia": row.data_mov,
            "obs": row.descricao_soma or row.descricao or "DEPÓSITO",
        }

        # Snapshot antes do POST: o ID novo é identificado por diferença, nunca
        # por "primeira transferência" da tabela.
        before_ids = {t.transfer_id for t in self.find_transfers(row)[0]}

        resp, data = self._submit_app(self.TRANSFERENCIAS_ENDPOINT, payload)
        status = self._json_status(data)
        if status != 1:
            message = self.TRANSFER_STATUS_MESSAGES.get(status) or (
                f"SOMA recusou a transferência (status={status})" if status is not None
                else (self._http_error(resp) or "Resposta inválida do SOMA ao gravar a transferência")
            )
            logger.error("Transferência linha %s: %s | resposta=%s | %s", row.row_number, message, data, self._describe_response(resp))
            return OperationOutcome(False, "", "Transferência", row.row_number, int((time.perf_counter() - t0) * 1000), error_message=message)

        # Gravação confirmada pelo SOMA (status 1). Identifica o ID para DADOS DOC.
        new_ids: List[str] = []
        for attempt in range(self._confirmation_attempts):
            _, exact = self.find_transfers(row)
            new_ids = [t.transfer_id for t in exact if t.transfer_id not in before_ids]
            if new_ids:
                break
            if attempt < self._confirmation_attempts - 1:
                time.sleep(1)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if len(new_ids) != 1:
            logger.warning("Transferência linha %s gravada (status 1) mas ID não identificado (novos: %s)", row.row_number, new_ids)
        ref = f"Transferência {new_ids[0]}" if len(new_ids) == 1 else "Transferência"
        return OperationOutcome(
            success=True,
            doc_id=TRANSFER_DOC_MARKER,
            tipo="Transferência",
            row_number=row.row_number,
            elapsed_ms=elapsed_ms,
            dados_doc=f"{ref} de {row.caixa_saida} para {row.caixa} registada no SOMA.",
        )
