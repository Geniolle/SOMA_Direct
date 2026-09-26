from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.repasse_mvv_service import (
    BaseReceitasMes,
    RepasseCalculado,
    RepasseMvvService,
    RepasseSubmissionResult,
    format_decimal_pt,
)

logger = logging.getLogger("soma_direct.repasse_mvv_orchestrator")


@dataclass(frozen=True)
class RepassePreview:
    bases: BaseReceitasMes
    itens: List[RepasseCalculado]
    total_repasse: Decimal
    existentes_soma: List[Dict[str, Any]]


class RepasseMvvOrchestrator:
    """Orquestra a preparação, cálculo e envio dos Repasses MVV."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or Settings.from_env()
        self.http = ResilientSession(
            timeout=self.settings.timeout_seconds,
            verify_tls=self.settings.verify_tls,
        )
        self.auth = SomaAuthenticator(self.settings, self.http)
        self.service = RepasseMvvService(self.settings, self.http)

    def initialize(self) -> None:
        """Autentica na sessão SOMA."""
        if not self.auth.login():
            raise RuntimeError("Não foi possível autenticar no SOMA com as credenciais configuradas.")

    def preparar_preview(
        self,
        ano: int,
        mes: int,
        doacoes_override: Optional[Decimal] = None,
        lanchonete_override: Optional[Decimal] = None,
        livraria_override: Optional[Decimal] = None,
    ) -> RepassePreview:
        """Obtém as bases de cálculo e gera a simulação dos 5 repasses."""
        self.initialize()

        # 1. Se fornecidas bases manuais, usa-as
        if doacoes_override is not None:
            bases = BaseReceitasMes(
                ano=ano,
                mes=mes,
                doacoes_dizimos_ofertas=doacoes_override,
                receitas_lanchonete=lanchonete_override or Decimal("0.00"),
                receitas_livraria=livraria_override or Decimal("0.00"),
                total_entrada=doacoes_override + (lanchonete_override or Decimal("0.00")) + (livraria_override or Decimal("0.00")),
                origem="MANUAL",
            )
        else:
            # Tenta buscar diretamente do Balancete do SOMA
            logger.info("Buscando bases de receitas no Balancete do SOMA para %02d/%d...", mes, ano)
            soma_bases = self.service.fetch_bases_from_soma(ano, mes)
            if soma_bases is not None:
                bases = soma_bases
            else:
                raise ValueError(
                    f"Não foram encontradas receitas no Balancete do SOMA para o período {mes:02d}/{ano}. "
                    "Por favor, informe os valores de entrada manualmente."
                )

        # 2. Calcula os 5 repasses
        itens = self.service.calcular_repasses(bases)
        total_repasse = sum((item.valor for item in itens), Decimal("0.00"))

        # 3. Consulta se já existem repasses cadastrados para o mês
        existentes = self.service.fetch_existing_repasses(ano, mes)

        return RepassePreview(
            bases=bases,
            itens=itens,
            total_repasse=total_repasse,
            existentes_soma=existentes,
        )

    def executar_submissao(
        self,
        itens: List[RepasseCalculado],
        ano: int,
        mes: int,
        data_repasse: Optional[str] = None,
        dry_run: bool = False,
    ) -> List[RepasseSubmissionResult]:
        """Submete a lista de repasses para o SOMA."""
        self.initialize()
        resultados: List[RepasseSubmissionResult] = []

        for item in itens:
            logger.info("Processando %s (Valor: %s €)...", item.plano_nome, format_decimal_pt(item.valor))
            res = self.service.submeter_repasse(
                item=item,
                ano=ano,
                mes=mes,
                data_repasse=data_repasse,
                dry_run=dry_run,
            )
            resultados.append(res)

        return resultados
