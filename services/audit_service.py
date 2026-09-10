from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config.settings import Settings
from core.http_session import ResilientSession
from domain.models import (
    AuditOutcome,
    CascadeAuditOutcome,
    ContaOrdemRow,
    SomaSearchResult,
    TipoMovimento,
    clean_amount_for_comparison,
    is_entrada_ou_saida,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
    strip_suffix_n,
    extract_suffix_n,
    validate_dados_doc,
)
from services.sheets_service import GoogleSheetsService

logger = logging.getLogger("soma_direct.audit")


class AuditService:
    """Serviço de alta performance para conciliação e auditoria de lançamentos SOMA via HTTP direto."""

    def __init__(self, settings: Optional[Settings] = None, http: Optional[ResilientSession] = None, sheets: Optional[GoogleSheetsService] = None):
        self.settings = settings
        self.http = http
        self.sheets = sheets
        self.base_url = (settings.site_base_url.rstrip("/") + "/") if settings else ""
        self.used_docs_in_run: set[str] = set()

    def _parse_search_table(self, html: str) -> List[SomaSearchResult]:
        results: List[SomaSearchResult] = []
        for rw in re.findall(r'<tr\b[^>]*>(.*?)</tr>', html, re.DOTALL):
            cells = [
                re.sub(r'<[^>]+>', ' ', c).strip()
                for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', rw, re.DOTALL)
            ]
            if len(cells) >= 9:
                codigo = cells[2]
                if codigo.lower() == "codigo":
                    continue
                results.append(
                    SomaSearchResult(
                        codigo=codigo,
                        tipo=cells[3],
                        descricao=cells[4],
                        valor=cells[5],
                        data=cells[6],
                        status=cells[7],
                        baixa=cells[8],
                    )
                )
        return results

    def search_by_codigo(self, doc_soma: str) -> Optional[SomaSearchResult]:
        doc_clean = normalize_document_value(doc_soma)
        if not doc_clean:
            return None

        payload = {
            "pesquisa": doc_clean,
            "filtro": "codigo",
            "id_inst": self.settings.institution_id,
            "tipo": "2",
            "v": "0",
            "s": "2",
            "t_d": "1",
            "cc": "-1",
            "c": "",
        }
        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=payload)
        items = self._parse_search_table(resp.text)
        for item in items:
            if normalize_document_value(item.codigo) == doc_clean:
                return item
        return items[0] if items else None

    def search_by_descricao(self, descricao: str, data_mov: str = "") -> List[SomaSearchResult]:
        desc_clean = str(descricao or "").strip()
        if not desc_clean:
            return []

        payload = {
            "pesquisa": desc_clean,
            "filtro": "descricao",
            "id_inst": self.settings.institution_id,
            "tipo": "2",
            "v": "0",
            "s": "2",
            "t_d": "1",
            "cc": "-1",
            "c": "",
        }
        if data_mov:
            d_norm = normalize_date_str(data_mov)
            payload["i"] = d_norm
            payload["f"] = d_norm
            payload["v"] = "1"

        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=payload)
        items = self._parse_search_table(resp.text)
        if not items and data_mov:
            payload["t_d"] = "0"
            resp0 = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=payload)
            items = self._parse_search_table(resp0.text)
        return items

    def search_by_periodo(self, data_mov: str) -> List[SomaSearchResult]:
        d_norm = normalize_date_str(data_mov)
        if not d_norm:
            return []

        payload = {
            "pesquisa": "",
            "filtro": "descricao",
            "id_inst": self.settings.institution_id,
            "tipo": "2",
            "v": "1",
            "s": "2",
            "t_d": "1",
            "cc": "-1",
            "c": "",
            "i": d_norm,
            "f": d_norm,
        }
        resp = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=payload)
        items = self._parse_search_table(resp.text)
        payload["t_d"] = "0"
        resp0 = self.http.post_ajax(f"{self.base_url}sys/post/buscarEntradasSaidas.php", data=payload)
        items0 = self._parse_search_table(resp0.text)
        seen = set()
        unified = []
        for it in items + items0:
            if it.codigo not in seen:
                seen.add(it.codigo)
                unified.append(it)
        return unified

    def fetch_dados_doc(self, doc_id: str) -> str:
        doc_clean = normalize_document_value(doc_id)
        if not doc_clean:
            return ""

        url = f"{self.base_url}?mod=ivv&exec=entradas_saidas_dados&ID={doc_clean}"
        try:
            resp = self.http.get(url)
            rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', resp.text, re.DOTALL)
            for r in rows:
                tds = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<td\b[^>]*>(.*?)</td>', r, re.DOTALL)]
                if len(tds) >= 4 and "Registrado" in tds[3]:
                    return tds[3]
            for r in rows:
                tds = [re.sub(r'<[^>]+>', ' ', c).strip() for c in re.findall(r'<td\b[^>]*>(.*?)</td>', r, re.DOTALL)]
                if len(tds) >= 4 and ("Baixa realizada" in tds[3] or "Caixa" in tds[3]):
                    return tds[3]
            return ""
        except Exception as e:
            logger.warning(f"Falha ao buscar DADOS DOC para {doc_clean}: {e}")
            return ""

    def insert_soma_payment(
        self,
        doc_id: str,
        data_pagamento: str,
        valor: str,
        caixa_str: str = "",
        forma_str: str = "",
    ) -> bool:
        """Insere pagamento para um lançamento 'EM ABERTO' no portal SOMA.

        Navega até o registro, preenche a data com a data da sheet CONTAORDEM do registro
        em execução, clica em salvar (POST para sys/app/pagamentos.php), valida o popup
        de sucesso (status 1) e volta para revalidar no SOMA.
        """
        doc_clean = normalize_document_value(doc_id)
        if not doc_clean:
            return False

        try:
            url_get = f"{self.base_url}?mod=ivv&exec=entradas_saidas_dados&ID={doc_clean}"
            r_get = self.http.get(url_get)
            if r_get.status_code != 200:
                logger.error(f"Erro ao acessar documento {doc_clean}: HTTP {r_get.status_code}")
                return False

            fluxo_valor_m = re.search(r'name=["\']fluxo_valor["\']\s+value=["\'](.*?)["\']', r_get.text)
            fluxo_valor = fluxo_valor_m.group(1) if fluxo_valor_m else clean_amount_for_comparison(valor)

            caixas = re.findall(r'<select\b[^>]*name=["\']id_caixa["\'][^>]*>(.*?)</select>', r_get.text, re.DOTALL)
            caixa_opts = re.findall(r'<option\b[^>]*value=["\'](\d+)["\'][^>]*>(.*?)</option>', caixas[0]) if caixas else []

            id_caixa = ""
            target_cx = norm_basic(caixa_str)
            if target_cx:
                for opt_id, opt_text in caixa_opts:
                    if target_cx in norm_basic(opt_text) or norm_basic(opt_text) in target_cx:
                        id_caixa = opt_id
                        break
            if not id_caixa and caixa_opts:
                id_caixa = caixa_opts[0][0]
            if not id_caixa:
                id_caixa = "1226"

            target_forma = norm_basic(forma_str)
            if "transf" in target_forma:
                forma_val = "3"
            elif "dep" in target_forma:
                forma_val = "1"
            elif "pix" in target_forma:
                forma_val = "4"
            elif "cheq" in target_forma:
                forma_val = "2"
            elif "cart" in target_forma or "maquin" in target_forma:
                forma_val = "5"
            else:
                forma_val = "0"

            d_norm = normalize_date_str(data_pagamento)
            id_fluxo = f"{int(doc_clean):010d}"

            payload = {
                "fluxo_desconto": "0,00",
                "fluxo_valor": fluxo_valor,
                "data_pagamento": d_norm,
                "forma_pagamento": forma_val,
                "aplicar_desconto": "0",
                "num_cheque": "",
                "num_documento": "",
                "tipo_pagamento": "0",
                "valor_pagamento": fluxo_valor,
                "id_caixa": id_caixa,
                "id_fluxo": id_fluxo,
                "add": "1",
                "aceitar_caixa_negativo": "1",
            }

            url_post = f"{self.base_url}sys/app/pagamentos.php"
            resp = self.http.post(url_post, data=payload)
            ok = False
            try:
                res_json = resp.json()
                if res_json.get("status") == 5:
                    open_period_date = datetime.now().strftime("%d/%m/%Y")
                    if d_norm != open_period_date:
                        logger.warning(
                            "Mês de %s fechado para pagamento do DOC %s; tentando compensação em %s.",
                            d_norm,
                            doc_clean,
                            open_period_date,
                        )
                        d_norm = open_period_date
                        payload["data_pagamento"] = d_norm
                        resp = self.http.post(url_post, data=payload)
                        res_json = resp.json()
                if res_json.get("status") in (1, 4) or res_json.get("pago") == 1 or res_json.get("status") == 8:
                    ok = True
                    logger.info(f"Pagamento para DOC {doc_clean} registrado/verificado no SOMA ({res_json}).")
            except Exception:
                if resp.status_code == 200 and ('"status":1' in resp.text or '"pago":1' in resp.text):
                    ok = True

            if not ok:
                logger.warning(f"Resposta ao registrar pagamento do DOC {doc_clean}: {resp.text[:150]}")

            # 2. Realizar a baixa do pagamento (sys/app/baixas.php)
            time.sleep(0.3)
            r_get2 = self.http.get(url_get)
            baixas = re.findall(r'class="[^"]*inst_baixa[^"]*"[^>]*id="(\d+)"', r_get2.text)
            if not baixas:
                baixas = re.findall(r'id="(\d+)"[^>]*class="[^"]*inst_baixa', r_get2.text)

            for id_baixa in baixas:
                payload_baixa = {
                    "id_pagamento_baixa": id_baixa,
                    "data_baixa": d_norm,
                    "add": "1",
                    "aceitar_caixa_negativo": "1",
                    "num_doc_baixa": "",
                }
                r_baixa = self.http.post(f"{self.base_url}sys/app/baixas.php", data=payload_baixa)
                try:
                    res_b = r_baixa.json()
                    if res_b.get("status") in (1, 4):
                        logger.info(f"Baixa para ID {id_baixa} (DOC {doc_clean}) realizada com sucesso ({res_b}).")
                except Exception:
                    pass

            # Voltar e revalidar no SOMA
            time.sleep(0.5)
            verify = self.search_by_codigo(doc_clean)
            if verify and (norm_basic(verify.status) == "pago" or norm_basic(verify.baixa) == "sim"):
                logger.info(f"DOC {doc_clean} agora está com status PAGO e baixa SIM no SOMA.")
                return True
            return ok
        except Exception as e:
            logger.exception(f"Erro ao inserir pagamento para DOC {doc_clean} no SOMA: {e}")
            return False

    def validate_soma_record(
        self,
        row: ContaOrdemRow,
        result: Optional[SomaSearchResult],
    ) -> Tuple[str, List[str]]:
        """
        Valida as colunas do site SOMA contra os valores da folha:
        - Coluna 3: Código vs DOC. SOMA
        - Coluna 4: Tipo vs TIPO
        - Coluna 5: Descrição vs DESCRIÇÃO SOMA
        - Coluna 6: Valor vs IMPORTÂNCIA
        - Coluna 7: Vencimento / Entrada vs DATA MOV.
        - Coluna 8: Status (fixo esperado: PAGO)
        - Coluna 9: Baixa (fixo esperado: SIM)
        """
        if result is None:
            return "Inconsistente", ["Registo não encontrado no portal SOMA"]

        inconsistencias: List[str] = []

        doc_esperado = normalize_document_value(row.doc_soma)
        doc_site = normalize_document_value(result.codigo)
        if not doc_esperado or doc_esperado != doc_site:
            inconsistencias.append(f"CÓDIGO (col 3): site='{doc_site}' != sheet='{doc_esperado}'")

        tipo_esperado = norm_basic(row.tipo.value)
        tipo_site = norm_basic(result.tipo)
        if tipo_esperado != tipo_site:
            inconsistencias.append(f"TIPO (col 4): site='{result.tipo}' != sheet='{row.tipo.value}'")

        desc_esperada = norm_basic(row.descricao_soma or row.descricao)
        desc_site = norm_basic(result.descricao)
        if desc_esperada != desc_site:
            inconsistencias.append(f"DESCRIÇÃO (col 5): site='{result.descricao}' != sheet='{desc_esperada}'")

        valor_esperado = clean_amount_for_comparison(row.importancia)
        valor_site = clean_amount_for_comparison(result.valor)
        if valor_esperado != valor_site:
            inconsistencias.append(f"VALOR (col 6): site='{valor_site}' != sheet='{valor_esperado}'")

        data_esperada = normalize_date_str(row.data_mov)
        data_site = normalize_date_str(result.data)
        if data_esperada != data_site:
            inconsistencias.append(f"DATA (col 7): site='{data_site}' != sheet='{data_esperada}'")

        if norm_basic(result.status) != "pago":
            inconsistencias.append(f"STATUS (col 8): site='{result.status}' != esperado='PAGO'")

        if norm_basic(result.baixa) != "sim":
            inconsistencias.append(f"BAIXA (col 9): site='{result.baixa}' != esperado='SIM'")

        if inconsistencias:
            return "Inconsistente", inconsistencias

        return "Confirmado", []

    def matches_ignoring_code_and_suffix(
        self,
        row: ContaOrdemRow,
        result: Optional[SomaSearchResult],
    ) -> Tuple[bool, bool]:
        """
        Verifica se o registro bate nos campos essenciais:
        - Tipo, Valor, Data, Status==PAGO, Baixa==SIM
        - Descrição base (sem sufixo Nxxx)
        Retorna (matches: bool, desc_differs: bool)
        """
        if result is None:
            return False, False

        if norm_basic(row.tipo.value) != norm_basic(result.tipo):
            return False, False

        if clean_amount_for_comparison(row.importancia) != clean_amount_for_comparison(result.valor):
            return False, False

        if normalize_date_str(row.data_mov) != normalize_date_str(result.data):
            return False, False

        if norm_basic(result.status) != "pago":
            return False, False

        if norm_basic(result.baixa) != "sim":
            return False, False

        base_esperada = norm_basic(strip_suffix_n(row.descricao_soma or row.descricao))
        base_site = norm_basic(strip_suffix_n(result.descricao))
        if base_esperada != base_site:
            return False, False

        desc_differs = norm_basic(row.descricao_soma or row.descricao) != norm_basic(result.descricao)
        return True, desc_differs

    def _process_dados_doc_and_finalize(
        self,
        row: ContaOrdemRow,
        matched_doc: str,
        new_desc: Optional[str] = None,
        is_correction: bool = False,
    ) -> AuditOutcome:
        doc_clean = normalize_document_value(matched_doc)
        if doc_clean:
            self.used_docs_in_run.add(doc_clean)

        sheet_dados = str(row.dados_doc or "").strip()
        dados_doc_site = ""

        # Se já temos DADOS DOC na sheet e ele valida com Caixa e Forma, usamos diretamente (hiper-rápido)
        dados_valido = False
        dados_err = None
        if sheet_dados and not is_correction:
            dados_valido, dados_err = validate_dados_doc(
                dados_doc=sheet_dados,
                sheet_caixa=row.caixa,
                sheet_forma=row.forma_pagamento,
            )

        # Se não temos ou a validação local falhou/era correção, consulta a página de detalhes no portal
        if not dados_valido:
            dados_doc_site = self.fetch_dados_doc(matched_doc)
            dados_valido, dados_err = validate_dados_doc(
                dados_doc=dados_doc_site or sheet_dados,
                sheet_caixa=row.caixa,
                sheet_forma=row.forma_pagamento,
            )

        if not dados_valido:
            logger.warning(f"Linha {row.row_number}: Falha Caixa/Forma em DADOS DOC ({dados_err})")
            return AuditOutcome(
                analyzed=True,
                inconsistent=True,
                inconsistencies=[dados_err or "DADOS DOC inválido"],
                dados_doc=dados_doc_site or sheet_dados,
            )

        if is_correction:
            logger.info(
                f"Linha {row.row_number}: Corrigido! DOC '{row.doc_soma}' -> '{matched_doc}', "
                f"DESC '{row.descricao_soma}' -> '{new_desc or row.descricao_soma}'"
            )
            return AuditOutcome(
                analyzed=True,
                corrected=True,
                new_doc=matched_doc,
                new_desc=new_desc,
                dados_doc=dados_doc_site or sheet_dados,
            )

        logger.info(f"Linha {row.row_number}: Confirmado com sucesso! (DOC {matched_doc})")
        return AuditOutcome(
            analyzed=True,
            confirmed=True,
            dados_doc=dados_doc_site or sheet_dados,
        )


    def audit_row(self, row: ContaOrdemRow) -> AuditOutcome:
        """Audita uma única linha seguindo a sequência estrita:
        1. FASE 1: Pesquisa por Descrição + Data (Modo C - início da pesquisa)
        2. FASE 2: Pesquisa por Lote de Data (Modo B - se Fase 1 não encontrar)
        3. FASE 3: Pesquisa por DOC. SOMA (Modo A - última fase)
        
        Avança para o próximo registo assim que encontrar/corrigir,
        só passando à fase seguinte no mesmo registo se a anterior falhar.
        """
        if not is_entrada_ou_saida(row.tipo):
            logger.info("Linha %s ignorada: TIPO '%s' não é Entrada ou Saída", row.row_number, row.tipo.value if row.tipo else "")
            return AuditOutcome(analyzed=False, confirmed=True)

        doc_soma = normalize_document_value(row.doc_soma)
        target_val = clean_amount_for_comparison(row.importancia)
        target_desc = norm_basic(row.descricao_soma or row.descricao)
        inconsistencias: List[str] = []

        # =========================================================================
        # FASE 1: Pesquisa por Descrição + Data (Modo C - Início da Pesquisa)
        # =========================================================================
        desc_search_term = row.descricao_soma or row.descricao
        if desc_search_term:
            terms_to_try = [desc_search_term]
            stripped = strip_suffix_n(desc_search_term)
            if stripped != desc_search_term:
                terms_to_try.append(stripped)

            for term in terms_to_try:
                desc_candidates = self.search_by_descricao(term, data_mov=row.data_mov)
                if desc_candidates:
                    for cand in desc_candidates:
                        cand_code = normalize_document_value(cand.codigo)
                        if cand_code in self.used_docs_in_run and cand_code != doc_soma:
                            continue

                        if "EM ABERTO" in (cand.status or "").upper():
                            cand_val = clean_amount_for_comparison(cand.valor)
                            if cand_val == target_val and norm_basic(cand.tipo) == norm_basic(row.tipo.value):
                                logger.info(
                                    "Linha %s: Lançamento DOC %s em aberto no SOMA. Inserindo pagamento com data %s...",
                                    row.row_number, cand.codigo, row.data_mov,
                                )
                                self.insert_soma_payment(
                                    doc_id=cand.codigo,
                                    data_pagamento=row.data_mov,
                                    valor=row.importancia,
                                    caixa_str=row.caixa,
                                    forma_str=row.forma_pagamento,
                                )
                                refreshed = self.search_by_codigo(cand.codigo)
                                if refreshed:
                                    cand = refreshed

                        cand_audit, _ = self.validate_soma_record(row, cand)
                        if cand_audit == "Confirmado":
                            is_corr = bool(doc_soma and doc_soma != normalize_document_value(cand.codigo))
                            return self._process_dados_doc_and_finalize(
                                row=row,
                                matched_doc=normalize_document_value(cand.codigo),
                                is_correction=is_corr,
                            )
                        matched, desc_differs = self.matches_ignoring_code_and_suffix(row, cand)
                        if matched:
                            row_seq = extract_suffix_n(row.descricao_soma or row.descricao)
                            cand_seq = extract_suffix_n(cand.descricao)
                            if row_seq is not None and cand_seq is not None and row_seq != cand_seq:
                                if doc_soma and cand_code == doc_soma:
                                    return self._process_dados_doc_and_finalize(
                                        row=row,
                                        matched_doc=cand_code,
                                        new_desc=cand.descricao if desc_differs else None,
                                        is_correction=True,
                                    )
                                continue
                            return self._process_dados_doc_and_finalize(
                                row=row,
                                matched_doc=normalize_document_value(cand.codigo),
                                new_desc=cand.descricao if desc_differs else None,
                                is_correction=True,
                            )

        # =========================================================================
        # FASE 2: Pesquisa por Lote de Data (Modo B - se Fase 1 não encontrou)
        # =========================================================================
        if row.data_mov:
            periodo_candidates = self.search_by_periodo(row.data_mov)
            if periodo_candidates:
                # 1. Match de valor, tipo e descrição exata
                for cand in periodo_candidates:
                    cand_code = normalize_document_value(cand.codigo)
                    if cand_code in self.used_docs_in_run and cand_code != doc_soma:
                        continue

                    if "EM ABERTO" in (cand.status or "").upper():
                        cand_val = clean_amount_for_comparison(cand.valor)
                        if cand_val == target_val and norm_basic(cand.tipo) == norm_basic(row.tipo.value):
                            self.insert_soma_payment(
                                doc_id=cand.codigo,
                                data_pagamento=row.data_mov,
                                valor=row.importancia,
                                caixa_str=row.caixa,
                                forma_str=row.forma_pagamento,
                            )
                            refreshed = self.search_by_codigo(cand.codigo)
                            if refreshed:
                                cand = refreshed

                    cand_audit, _ = self.validate_soma_record(row, cand)
                    if cand_audit == "Confirmado":
                        is_corr = bool(doc_soma and doc_soma != normalize_document_value(cand.codigo))
                        return self._process_dados_doc_and_finalize(
                            row=row,
                            matched_doc=normalize_document_value(cand.codigo),
                            is_correction=is_corr,
                        )
                    matched, desc_differs = self.matches_ignoring_code_and_suffix(row, cand)
                    if matched and norm_basic(cand.descricao) == target_desc:
                        return self._process_dados_doc_and_finalize(
                            row=row,
                            matched_doc=normalize_document_value(cand.codigo),
                            new_desc=cand.descricao if desc_differs else None,
                            is_correction=True,
                        )

                # 2. Match semântico no lote de data (avaliando lote por valor, tipo e descrição base)
                semantic_candidates = []
                for cand in periodo_candidates:
                    cand_code = normalize_document_value(cand.codigo)
                    if cand_code in self.used_docs_in_run and cand_code != doc_soma:
                        continue

                    matched, desc_differs = self.matches_ignoring_code_and_suffix(row, cand)
                    if matched:
                        semantic_candidates.append((cand, desc_differs))

                if semantic_candidates:
                    row_seq = extract_suffix_n(row.descricao_soma or row.descricao)

                    # Prioridade A: DOC. SOMA coincide com um dos candidatos
                    if doc_soma:
                        for cand, desc_differs in semantic_candidates:
                            if normalize_document_value(cand.codigo) == doc_soma:
                                return self._process_dados_doc_and_finalize(
                                    row=row,
                                    matched_doc=doc_soma,
                                    new_desc=cand.descricao if desc_differs else None,
                                    is_correction=True,
                                )

                    # Prioridade B: Sequencial Nxxx coincide exatamente
                    for cand, desc_differs in semantic_candidates:
                        cand_seq = extract_suffix_n(cand.descricao)
                        if row_seq is not None and cand_seq is not None and row_seq == cand_seq:
                            return self._process_dados_doc_and_finalize(
                                row=row,
                                matched_doc=normalize_document_value(cand.codigo),
                                new_desc=cand.descricao if desc_differs else None,
                                is_correction=True,
                            )

                    # Prioridade C: Candidato único no lote de data com mesmo valor e descrição base
                    if len(semantic_candidates) == 1:
                        cand, desc_differs = semantic_candidates[0]
                        return self._process_dados_doc_and_finalize(
                            row=row,
                            matched_doc=normalize_document_value(cand.codigo),
                            new_desc=cand.descricao if desc_differs else None,
                            is_correction=True,
                        )

        # =========================================================================
        # FASE 3: Pesquisa por DOC. SOMA (Modo A - última fase)
        # =========================================================================
        if doc_soma and doc_soma.isdigit():
            search_result = self.search_by_codigo(doc_soma)
            if search_result is not None:
                if "EM ABERTO" in (search_result.status or "").upper():
                    logger.info(
                        "Linha %s: Lançamento DOC %s em aberto no SOMA. Inserindo pagamento com data %s...",
                        row.row_number, doc_soma, row.data_mov,
                    )
                    self.insert_soma_payment(
                        doc_id=doc_soma,
                        data_pagamento=row.data_mov,
                        valor=row.importancia,
                        caixa_str=row.caixa,
                        forma_str=row.forma_pagamento,
                    )
                    search_result = self.search_by_codigo(doc_soma)

                auditoria, inconsistencias = self.validate_soma_record(row, search_result)
                if auditoria == "Confirmado":
                    return self._process_dados_doc_and_finalize(
                        row=row,
                        matched_doc=normalize_document_value(search_result.codigo),
                    )

                # Se falhou por divergência no sequencial/descrição na folha,
                # mas o DOC existe e Tipo, Valor, Data, Status PAGO e Baixa SIM batem 100%:
                matched, desc_differs = self.matches_ignoring_code_and_suffix(row, search_result)
                if matched and desc_differs:
                    logger.info(
                        "Linha %s: DOC %s bate Tipo, Valor e Data, mas sequencial difere ('%s' vs '%s'). "
                        "Atualizando descrição na folha conforme SOMA.",
                        row.row_number,
                        doc_soma,
                        row.descricao_soma or row.descricao,
                        search_result.descricao,
                    )
                    return self._process_dados_doc_and_finalize(
                        row=row,
                        matched_doc=normalize_document_value(search_result.codigo),
                        new_desc=search_result.descricao,
                        is_correction=True,
                    )
            else:
                inconsistencias = [f"Código {doc_soma} não existe no SOMA"]
        else:
            if not doc_soma:
                inconsistencias = ["DOC. SOMA não preenchido e não localizado por descrição nem lote de data"]
            else:
                inconsistencias = [f"DOC não numérico ('{doc_soma}')"]

        # =========================================================================
        # Se falhar as 3 fases: registra inconsistência e avança para o próximo registo
        # =========================================================================
        return AuditOutcome(
            analyzed=True,
            inconsistent=True,
            inconsistencies=inconsistencias or ["Registo não localizado no portal SOMA nas 3 fases de pesquisa"],
        )

    def audit_all(
        self,
        limit: Optional[int] = None,
        batch_size: int = 25,
        update_sheet: bool = True,
    ) -> Dict[str, Any]:
        """Executa auditoria em lote com atualização periódica e métricas de performance."""
        t0 = time.perf_counter()
        logger.info("Carregando linhas pendentes de auditoria da planilha...")
        rows = self.sheets.get_auditable_rows()

        if limit and limit > 0:
            rows = rows[:limit]

        total_rows = len(rows)
        logger.info(f"Total de linhas a auditar: {total_rows}")
        print(f"\n[AUDITORIA SOMA] Total de linhas selecionadas: {total_rows}\n", flush=True)

        stats = {
            "total": total_rows,
            "confirmed": 0,
            "corrected": 0,
            "inconsistent": 0,
            "errors": 0,
        }

        updates_buffer: List[Dict[str, Any]] = []

        for idx, row in enumerate(rows, start=1):
            t_row = time.perf_counter()
            try:
                outcome = self.audit_row(row)
                if outcome.confirmed:
                    stats["confirmed"] += 1
                    status_str = None
                    aud_str = "Confirmado"
                elif outcome.corrected:
                    stats["corrected"] += 1
                    status_str = None
                    aud_str = "Corrigido"
                else:
                    stats["inconsistent"] += 1
                    status_str = "ERRO" if "DADOS DOC" in "; ".join(outcome.inconsistencies) else None
                    aud_str = "Inconsistente"

                updates_buffer.append({
                    "row_idx": row.row_number,
                    "auditoria": aud_str,
                    "new_doc": outcome.new_doc,
                    "new_desc": outcome.new_desc,
                    "dados_doc": outcome.dados_doc if outcome.dados_doc != row.dados_doc else None,
                    "status": status_str,
                })

                elapsed_ms = int((time.perf_counter() - t_row) * 1000)
                logger.debug(f"Linha {row.row_number} auditada em {elapsed_ms}ms -> {aud_str}")

            except Exception as e:
                stats["errors"] += 1
                logger.exception(f"Erro na auditoria da linha {row.row_number}: {e}")

            # Batch update a cada batch_size linhas
            if update_sheet and len(updates_buffer) >= batch_size:
                self.sheets.batch_update_audit_records(updates_buffer)
                updates_buffer.clear()

            # Feedback no console
            if idx % 10 == 0 or idx == total_rows:
                pct = (idx / total_rows) * 100 if total_rows > 0 else 100
                print(
                    f"Progresso: {idx}/{total_rows} ({pct:.1f}%) | "
                    f"Confirmados: {stats['confirmed']} | Corrigidos: {stats['corrected']} | "
                    f"Inconsistentes: {stats['inconsistent']} | Erros: {stats['errors']}",
                    flush=True,
                )

        # Atualiza o buffer restante
        if update_sheet and updates_buffer:
            self.sheets.batch_update_audit_records(updates_buffer)
            updates_buffer.clear()

        total_elapsed = time.perf_counter() - t0
        stats["total_elapsed_sec"] = round(total_elapsed, 2)
        stats["avg_per_row_sec"] = round(total_elapsed / total_rows, 3) if total_rows > 0 else 0

        print("\n" + "=" * 70)
        print("RESUMO DA AUDITORIA SOMA_DIRECT")
        print("=" * 70)
        print(f"Total analisado:   {stats['total']}")
        print(f"Confirmados:       {stats['confirmed']}")
        print(f"Corrigidos:        {stats['corrected']}")
        print(f"Inconsistentes:    {stats['inconsistent']}")
        print(f"Erros técnicos:    {stats['errors']}")
        print(f"Tempo total:       {stats['total_elapsed_sec']} segundos")
        print(f"Média por linha:   {stats['avg_per_row_sec']} segundos")
        print("=" * 70 + "\n")

        return stats

    def get_detailed_inconsistency_message(self, row: ContaOrdemRow) -> str:
        """Gera mensagem explicativa e concisa para a coluna AUDITORIA em vez de apenas 'Inconsistente'."""
        doc = normalize_document_value(row.doc_soma)

        # 1. Não numérico ou vazio
        if not doc:
            return "DOC. SOMA vazio na folha"
        if not doc.isdigit():
            return f"DOC não numérico ('{doc}')"

        # 2. Busca por código
        search_res = self.search_by_codigo(doc)
        if search_res is None:
            return f"Código {doc} não existe no SOMA"

        # 3. Validação das 7 colunas
        val_status, incs = self.validate_soma_record(row, search_res)
        if not incs:
            dados_site = self.fetch_dados_doc(doc)
            _, err = validate_dados_doc(dados_site or row.dados_doc, row.caixa, row.forma_pagamento)
            if err:
                return err[:85]
            return "DADOS DOC inválido ou ausente"

        has_val = any("VALOR" in i for i in incs)
        has_desc = any("DESCRIÇÃO" in i for i in incs)
        has_tipo = any("TIPO" in i for i in incs)
        has_data = any("DATA" in i for i in incs)
        has_status = any("STATUS" in i for i in incs)
        has_baixa = any("BAIXA" in i for i in incs)

        if has_status or has_baixa:
            return f"Não quitado no SOMA (Status={search_res.status} / Baixa={search_res.baixa})"

        if has_val and has_desc:
            desc_abbrev = search_res.descricao[:25].strip()
            return f"DOC cruzado (SOMA: {desc_abbrev} | {search_res.valor} €)"

        if has_tipo:
            return f"Tipo divergente: site={search_res.tipo} != sheet={row.tipo.value}"

        if has_val:
            return f"Valor divergente: site={search_res.valor} € != sheet={row.importancia} €"

        if has_desc:
            desc_abbrev = search_res.descricao[:30].strip()
            return f"Descrição divergente: site='{desc_abbrev}'"

        if has_data:
            return f"Data divergente: site={search_res.data} != sheet={row.data_mov}"

        return "; ".join(incs)[:75]

    def revalidate_inconsistencies(
        self,
        batch_size: int = 50,
        update_sheet: bool = True,
    ) -> Dict[str, Any]:
        """Revalida todas as linhas marcadas como 'Inconsistente' e substitui pelo motivo detalhado do erro."""
        t0 = time.perf_counter()
        logger.info("Carregando registros inconsistentes para revalidação detalhada...")

        all_rows = self.sheets.get_all_rows(only_entrada_saida=True)
        inconsistent_rows = [
            r for r in all_rows 
            if r.auditoria.strip() == "Inconsistente" and is_entrada_ou_saida(r.tipo)
        ]
        total_rows = len(inconsistent_rows)

        print(f"\n[REVALIDAÇÃO DE INCONSISTÊNCIAS] Total de linhas: {total_rows}\n", flush=True)

        stats = {
            "total": total_rows,
            "confirmed_now": 0,
            "corrected_now": 0,
            "detailed_errors": 0,
            "errors": 0,
        }

        updates_buffer: List[Dict[str, Any]] = []

        for idx, row in enumerate(inconsistent_rows, start=1):
            t_row = time.perf_counter()
            try:
                # 1. Tenta auditoria completa primeiro (caso agora passe como Confirmado ou Corrigido)
                outcome = self.audit_row(row)
                if outcome.confirmed:
                    stats["confirmed_now"] += 1
                    aud_text = "Confirmado"
                    new_doc = None
                    new_desc = None
                    status_str = None
                elif outcome.corrected:
                    stats["corrected_now"] += 1
                    aud_text = "Corrigido"
                    new_doc = outcome.new_doc
                    new_desc = outcome.new_desc
                    status_str = None
                else:
                    stats["detailed_errors"] += 1
                    aud_text = self.get_detailed_inconsistency_message(row)
                    new_doc = None
                    new_desc = None
                    status_str = "ERRO" if ("DADOS DOC" in aud_text or "CAIXA" in aud_text or "FORMA" in aud_text) else None

                updates_buffer.append({
                    "row_idx": row.row_number,
                    "auditoria": aud_text,
                    "new_doc": new_doc,
                    "new_desc": new_desc,
                    "dados_doc": outcome.dados_doc if outcome.dados_doc and outcome.dados_doc != row.dados_doc else None,
                    "status": status_str,
                })

                elapsed_ms = int((time.perf_counter() - t_row) * 1000)
                logger.debug(f"Linha {row.row_number} revalidada em {elapsed_ms}ms -> {aud_text}")

            except Exception as e:
                stats["errors"] += 1
                logger.exception(f"Erro na revalidação da linha {row.row_number}: {e}")

            if update_sheet and len(updates_buffer) >= batch_size:
                self.sheets.batch_update_audit_records(updates_buffer)
                updates_buffer.clear()

            if idx % 10 == 0 or idx == total_rows:
                pct = (idx / total_rows) * 100 if total_rows > 0 else 100
                print(
                    f"Progresso: {idx}/{total_rows} ({pct:.1f}%) | "
                    f"Confirmados: {stats['confirmed_now']} | Corrigidos: {stats['corrected_now']} | "
                    f"Erros detalhados: {stats['detailed_errors']}",
                    flush=True,
                )

        if update_sheet and updates_buffer:
            self.sheets.batch_update_audit_records(updates_buffer)
            updates_buffer.clear()

        total_elapsed = time.perf_counter() - t0
        stats["total_elapsed_sec"] = round(total_elapsed, 2)

        print("\n" + "=" * 70)
        print("RESUMO DA REVALIDAÇÃO DE INCONSISTÊNCIAS")
        print("=" * 70)
        print(f"Total revalidado:           {stats['total']}")
        print(f"Passaram para Confirmado:   {stats['confirmed_now']}")
        print(f"Passaram para Corrigido:    {stats['corrected_now']}")
        print(f"Atualizados com Erro Real:  {stats['detailed_errors']}")
        print(f"Tempo total:                {stats['total_elapsed_sec']} segundos")
        print("=" * 70 + "\n")

        return stats

    def audit_row_cascade(
        self,
        row: ContaOrdemRow,
        date_batch_items: Optional[List[SomaSearchResult]] = None,
        used_soma_codes: Optional[set[str]] = None,
        origin_doc: Optional[str] = None,
    ) -> CascadeAuditOutcome:
        """
        Hierarquia de Auditoria e Reconciliação em 4 Níveis:
        Nível 1: Validação Direta por Código (DOC. SOMA)
        Nível 2: Reconciliação por Lote de Datas (Date-Batch Allocation 1-para-1)
        Nível 3: Reconciliação Semântica e Descritiva (Normalização textual & sufixos Nxxx)
        Nível 4: Confronto com a Fonte de Origem (ID_INTERNO em T_EXTRATO, VC_VENDAS, etc.)
        """
        if used_soma_codes is None:
            used_soma_codes = set()

        tipo_str = norm_basic(row.tipo.value if row.tipo else "")
        doc_str = normalize_document_value(row.doc_soma)
        clean_val = clean_amount_for_comparison(row.importancia)
        desc_str = norm_basic(row.descricao_soma or row.descricao)

        # Fast-path: Movimentações Internas (Transferências, MVV e Cartão Consolidado)
        if tipo_str in ("transferencia", "mvv") or doc_str.lower() in ("transferido", "mvv"):
            return CascadeAuditOutcome(
                confirmed=True,
                level_resolved=1,
                auditoria="Confirmado",
                notes="Transferência Interna / MVV",
            )

        if doc_str.upper() == "PAGTO CARTAO" or tipo_str == "cartao" or "pag.cartao" in desc_str:
            return CascadeAuditOutcome(
                confirmed=True,
                level_resolved=1,
                auditoria="Confirmado",
                notes="Pagamento de Cartão Consolidado",
            )

        # Mapear itens da data
        soma_items_on_date = date_batch_items or []
        soma_codes_on_date = {it.codigo: it for it in soma_items_on_date}

        # -------------------------------------------------------------
        # NÍVEL 1: Checagem Direta por Código (DOC. SOMA)
        # -------------------------------------------------------------
        divergent_val_found = None
        if doc_str.isdigit():
            target_item: Optional[SomaSearchResult] = None
            if doc_str in soma_codes_on_date:
                target_item = soma_codes_on_date[doc_str]
            elif self.settings and self.http:
                target_item = self.search_by_codigo(doc_str)

            if target_item:
                site_val = clean_amount_for_comparison(target_item.valor)
                if site_val == clean_val:
                    used_soma_codes.add(doc_str)
                    return CascadeAuditOutcome(
                        confirmed=True,
                        level_resolved=1,
                        auditoria="Confirmado",
                        new_doc=doc_str,
                        new_desc=target_item.descricao,
                        notes="DOC direto conferido com sucesso",
                    )
                else:
                    divergent_val_found = target_item.valor

        # -------------------------------------------------------------
        # NÍVEL 2: Reconciliação por Lote de Datas (Date-Batch Matching)
        # -------------------------------------------------------------
        available_matches = [
            it for it in soma_items_on_date
            if it.codigo not in used_soma_codes and clean_amount_for_comparison(it.valor) == clean_val
        ]

        if len(available_matches) == 1:
            matched = available_matches[0]
            used_soma_codes.add(matched.codigo)
            is_corrigido = (doc_str != matched.codigo)
            return CascadeAuditOutcome(
                confirmed=True,
                corrected=is_corrigido,
                level_resolved=2,
                auditoria="Corrigido" if is_corrigido else "Confirmado",
                new_doc=matched.codigo,
                new_desc=matched.descricao,
                notes="DOC alocado 1-para-1 por Lote de Data",
            )

        # Se múltiplos matches de mesmo valor, desempata pelo Nível 3 (Semântica)
        if len(available_matches) > 1:
            # -------------------------------------------------------------
            # NÍVEL 3: Análise Semântica e Descritiva
            # -------------------------------------------------------------
            base_sheet = norm_basic(strip_suffix_n(row.descricao))
            best_cand = None
            for cand in available_matches:
                base_cand = norm_basic(strip_suffix_n(cand.descricao))
                if base_sheet in base_cand or base_cand in base_sheet:
                    best_cand = cand
                    break
            if not best_cand:
                best_cand = available_matches[0]

            used_soma_codes.add(best_cand.codigo)
            is_corrigido = (doc_str != best_cand.codigo)
            return CascadeAuditOutcome(
                confirmed=True,
                corrected=is_corrigido,
                level_resolved=3,
                auditoria="Corrigido" if is_corrigido else "Confirmado",
                new_doc=best_cand.codigo,
                new_desc=best_cand.descricao,
                notes="DOC alocado por proximidade semântica no lote",
            )

        # -------------------------------------------------------------
        # NÍVEL 4: Confronto com a Fonte de Origem (ID_INTERNO)
        # -------------------------------------------------------------
        if origin_doc is not None and not origin_doc.strip():
            return CascadeAuditOutcome(
                confirmed=False,
                level_resolved=4,
                auditoria="Pendente lançamento SOMA",
                notes="Origem sem DOC cadastrado",
            )

        if divergent_val_found:
            return CascadeAuditOutcome(
                confirmed=False,
                level_resolved=1,
                auditoria=f"Valor divergente: site={divergent_val_found} != sheet={row.importancia}",
                notes="DOC existe mas com valor diferente e sem match no lote",
            )

        if doc_str.isdigit():
            return CascadeAuditOutcome(
                confirmed=False,
                level_resolved=1,
                auditoria=f"Código {doc_str} não existe no SOMA",
                notes="DOC não encontrado no SOMA e sem substituto na data",
            )

        return CascadeAuditOutcome(
            confirmed=False,
            level_resolved=4,
            auditoria="Pendente lançamento SOMA",
            notes="Sem DOC e sem correspondência na data",
        )

