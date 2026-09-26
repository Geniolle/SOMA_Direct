from __future__ import annotations

import calendar
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

from config.settings import Settings
from core.http_session import ResilientSession
from services.repasse_service import RepasseReportService, month_period, strip_tags, normalize_plano_conta

logger = logging.getLogger("soma_direct.repasse_mvv")


def parse_decimal(value: Any) -> Decimal:
    """Converte valor monetário (string ou float) para Decimal com 2 casas."""
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    text = re.sub(r"[^\d,.-]", "", str(value or "")).strip()
    if not text:
        return Decimal("0.00")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Valor monetário inválido: '{value}'") from exc


def format_decimal_pt(val: Decimal) -> str:
    """Formata Decimal para string com vírgula (ex: 518,21)."""
    return format(val.quantize(Decimal("0.01")), "f").replace(".", ",")


@dataclass(frozen=True)
class BaseReceitasMes:
    ano: int
    mes: int
    doacoes_dizimos_ofertas: Decimal
    receitas_lanchonete: Decimal
    receitas_livraria: Decimal
    total_entrada: Decimal
    origem: str  # 'SOMA_BALANCETE' ou 'MANUAL'


@dataclass(frozen=True)
class RepasseCalculado:
    id_plano_contas: str
    plano_nome: str
    aliquota_desc: str
    conta_santander_id: str
    conta_santander_numero: str
    valor: Decimal
    detalhe_calculo: str


@dataclass(frozen=True)
class RepasseSubmissionResult:
    sucesso: bool
    item: RepasseCalculado
    repasse_id: Optional[str]
    mensagem: str


# Mapeamento do DOM de Novo Repasse para cada rubrica MVV
MAPA_PLANOS_MVV = [
    {
        "id_plano": "369",
        "nome": "DÍZIMOS 10%",
        "conta_id": "5",
        "conta_num": "13002676-4",
        "tipo_calculo": "dizimos",
    },
    {
        "id_plano": "370",
        "nome": "OFERTA COVV 2%",
        "conta_id": "6",
        "conta_num": "13002679-5",
        "tipo_calculo": "covv",
    },
    {
        "id_plano": "372",
        "nome": "OFERTA NOVAS OBRAS 2%",
        "conta_id": "8",
        "conta_num": "13002675-7",
        "tipo_calculo": "novas_obras",
    },
    {
        "id_plano": "371",
        "nome": "OFERTA MISSÕES 1%",
        "conta_id": "7",
        "conta_num": "13002680-5",
        "tipo_calculo": "missoes",
    },
    {
        "id_plano": "373",
        "nome": "REPASSE LIVRARIA 3%",
        "conta_id": "5",
        "conta_num": "13002676-4",
        "tipo_calculo": "livraria",
    },
]


class RepasseMvvService:
    """Serviço isolado para cálculo e submissão dos Repasses MVV no SOMA."""

    def __init__(self, settings: Settings, http: ResilientSession):
        self.settings = settings
        self.http = http
        self.base_url = settings.site_base_url.rstrip("/") + "/"
        self.reports = RepasseReportService(settings, http)

    def fetch_bases_from_soma(self, ano: int, mes: int) -> Optional[BaseReceitasMes]:
        """Extrai as 3 bases de receitas diretamente do Balancete de Entradas do SOMA."""
        period = month_period(ano, mes)
        try:
            html = self.reports.fetch_balancete_html(period)
        except Exception as e:
            logger.warning("Falha ao obter balancete do SOMA para %02d/%d: %s", mes, ano, e)
            return None

        doacoes = Decimal("0.00")
        lanchonete = Decimal("0.00")
        livraria = Decimal("0.00")
        total_entrada = Decimal("0.00")

        # Parsear linhas do balancete
        for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
            cells = [unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)]
            if len(cells) < 2:
                continue
            row_text = " ".join(cells)
            label = cells[0].strip()
            label_norm = normalize_plano_conta(label)
            val_str = cells[-1].strip()

            try:
                val = parse_decimal(val_str)
            except Exception:
                continue

            if "dizimos e ofertas" in label_norm or "doacoes" in label_norm:
                doacoes = val
            elif "lanchonete" in label_norm or "verbo cafe" in label_norm:
                lanchonete = val
            elif "livraria" in label_norm and "fornecedor" not in label_norm and "saida" not in row_text.lower():
                livraria = val
            elif "total entrada" in label_norm:
                total_entrada = val

        if doacoes == 0 and lanchonete == 0 and livraria == 0 and total_entrada == 0:
            return None

        if total_entrada == 0:
            total_entrada = doacoes + lanchonete + livraria

        return BaseReceitasMes(
            ano=ano,
            mes=mes,
            doacoes_dizimos_ofertas=doacoes,
            receitas_lanchonete=lanchonete,
            receitas_livraria=livraria,
            total_entrada=total_entrada,
            origem="SOMA_BALANCETE",
        )

    @staticmethod
    def calcular_repasses(bases: BaseReceitasMes) -> List[RepasseCalculado]:
        """Aplica a regra oficial MVV para calcular os 5 repasses institucionais."""
        doa = bases.doacoes_dizimos_ofertas
        lan = bases.receitas_lanchonete
        liv = bases.receitas_livraria

        # 1. Dízimos: 10% de Doações + 3% de Lanchonete
        dizimo_doa = (doa * Decimal("0.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        dizimo_lan = (lan * Decimal("0.03")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        val_dizimo = dizimo_doa + dizimo_lan

        # 2. Oferta COVV: 2% de Doações
        val_covv = (doa * Decimal("0.02")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 3. Oferta Novas Obras: 2% de Doações
        val_novas = (doa * Decimal("0.02")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 4. Oferta Missões: 1% de Doações
        val_missoes = (doa * Decimal("0.01")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 5. Repasse Livraria: 3% de Livraria
        val_livraria = (liv * Decimal("0.03")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        resultados = [
            RepasseCalculado(
                id_plano_contas="369",
                plano_nome="DÍZIMOS 10%",
                aliquota_desc="10% Doações + 3% Lanchonete",
                conta_santander_id="5",
                conta_santander_numero="13002676-4",
                valor=val_dizimo,
                detalhe_calculo=f"(10% de {format_decimal_pt(doa)}) + (3% de {format_decimal_pt(lan)}) = {format_decimal_pt(dizimo_doa)} + {format_decimal_pt(dizimo_lan)}",
            ),
            RepasseCalculado(
                id_plano_contas="370",
                plano_nome="OFERTA COVV 2%",
                aliquota_desc="2% Doações",
                conta_santander_id="6",
                conta_santander_numero="13002679-5",
                valor=val_covv,
                detalhe_calculo=f"2% de {format_decimal_pt(doa)}",
            ),
            RepasseCalculado(
                id_plano_contas="372",
                plano_nome="OFERTA NOVAS OBRAS 2%",
                aliquota_desc="2% Doações",
                conta_santander_id="8",
                conta_santander_numero="13002675-7",
                valor=val_novas,
                detalhe_calculo=f"2% de {format_decimal_pt(doa)}",
            ),
            RepasseCalculado(
                id_plano_contas="371",
                plano_nome="OFERTA MISSÕES 1%",
                aliquota_desc="1% Doações",
                conta_santander_id="7",
                conta_santander_numero="13002680-5",
                valor=val_missoes,
                detalhe_calculo=f"1% de {format_decimal_pt(doa)}",
            ),
            RepasseCalculado(
                id_plano_contas="373",
                plano_nome="REPASSE LIVRARIA 3%",
                aliquota_desc="3% Livraria",
                conta_santander_id="5",
                conta_santander_numero="13002676-4",
                valor=val_livraria,
                detalhe_calculo=f"3% de {format_decimal_pt(liv)}",
            ),
        ]
        return resultados

    def fetch_existing_repasses(self, ano: int, mes: int) -> List[Dict[str, Any]]:
        """Consulta o SOMA para listar os repasses já existentes daquele mês."""
        last_day = calendar.monthrange(ano, mes)[1]
        p_i = f"01/{mes:02d}/{ano}"
        p_f = f"{last_day:02d}/{mes:02d}/{ano}"

        url = f"{self.base_url}sys/post/buscarRepasses.php"
        resp = self.http.post_ajax(url, data={
            "p_i": p_i,
            "p_f": p_f,
            "id_inst": self.settings.institution_id,
            "s": "0",
            "f": "0",
        })

        existentes = []
        for m in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", resp.text, re.DOTALL | re.IGNORECASE):
            cells = [unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", m.group(1), re.DOTALL | re.IGNORECASE)]
            if len(cells) < 4:
                continue
            row_text = " ".join(cells)
            if "acoes" in normalize_plano_conta(row_text) and "descricao" in normalize_plano_conta(row_text):
                continue
            # Procura id do repasse
            id_match = re.search(r"check(\d+)", m.group(1)) or re.search(r"ID=(\d+)", m.group(1))
            repasse_id = id_match.group(1) if id_match else ""
            desc = cells[2] if len(cells) > 2 else ""
            val = cells[3] if len(cells) > 3 else ""
            existentes.append({
                "id": repasse_id,
                "descricao": desc,
                "valor": val,
                "periodo": cells[4] if len(cells) > 4 else "",
            })
        return existentes

    def submeter_repasse(
        self,
        item: RepasseCalculado,
        ano: int,
        mes: int,
        data_repasse: Optional[str] = None,
        dry_run: bool = False,
    ) -> RepasseSubmissionResult:
        """Envia um repasse individual para sys/app/repasses.php."""
        if not data_repasse:
            last_day = calendar.monthrange(ano, mes)[1]
            data_repasse = f"{last_day:02d}/{mes:02d}/{ano}"

        if dry_run:
            return RepasseSubmissionResult(
                sucesso=True,
                item=item,
                repasse_id=None,
                mensagem="Simulação (Dry-run) - registo não enviado ao SOMA",
            )

        payload = {
            "id_repasse": "",
            "id_inst": self.settings.institution_id,
            "id_plano_contas": item.id_plano_contas,
            "tipo": "1",  # 1 = Internacional
            "banco": "2",  # 2 = Santander
            "agencia": "2",  # 2 = 3082
            "conta": item.conta_santander_id,
            "tipo_conta": "1",  # 1 = Conta Corrente
            "numero_deposito": "",
            "data_repasse": data_repasse,
            "mes": str(mes),
            "ano": str(ano),
            "id_moeda": "2",  # 2 = EURO
            "valor": format_decimal_pt(item.valor),
            "obs": f"Repasse MVV {mes:02d}/{ano} - {item.plano_nome}",
            "add": "1",
        }

        url = f"{self.base_url}sys/app/repasses.php"
        resp = self.http.post(url, data=payload)

        # Trata resposta JSON
        body = resp.text.strip()
        json_match = re.search(r"\{.*\}", body, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                status = int(data.get("status", 0))
                if status == 1:
                    rep_id = str(data.get("f", ""))
                    return RepasseSubmissionResult(
                        sucesso=True,
                        item=item,
                        repasse_id=rep_id,
                        mensagem="Repasse gravado com sucesso no SOMA",
                    )
                else:
                    return RepasseSubmissionResult(
                        sucesso=False,
                        item=item,
                        repasse_id=None,
                        mensagem=f"SOMA recusou a gravação com código status={status}",
                    )
            except Exception as e:
                return RepasseSubmissionResult(
                    sucesso=False,
                    item=item,
                    repasse_id=None,
                    mensagem=f"Falha ao interpretar JSON do SOMA: {e}",
                )

        return RepasseSubmissionResult(
            sucesso=False,
            item=item,
            repasse_id=None,
            mensagem=f"Resposta não-JSON do SOMA (HTTP {resp.status_code}): {body[:150]}",
        )
