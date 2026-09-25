from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from services.repasse_service import (
    BalanceteSaidaItem,
    RepasseReportService,
    RepasseValidationRecord,
    format_decimal_pt,
    reconcile_repasse_items,
)
from services.sheets_service import GoogleSheetsService

logger = logging.getLogger("soma_direct.repasse_orchestrator")


class RepasseAuditOrchestrator:
    """Orquestra a auditoria read-only de Repasses contra o Balancete."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        sheets: Optional[GoogleSheetsService] = None,
    ):
        self.settings = settings or Settings.from_env()
        self.http = ResilientSession(
            timeout=self.settings.timeout_seconds,
            verify_tls=self.settings.verify_tls,
        )
        self.auth = SomaAuthenticator(self.settings, self.http)
        self.reports = RepasseReportService(self.settings, self.http)
        self.sheets = sheets

    def initialize(self, with_sheets: bool) -> None:
        if not self.auth.login():
            raise RuntimeError("Não foi possível autenticar no SOMA.")
        if with_sheets and self.sheets is None:
            self.sheets = GoogleSheetsService(self.settings)

    def run(self, ano: int, dry_run: bool = True, instituicao_id: Optional[str] = None) -> List[RepasseValidationRecord]:
        self.initialize(with_sheets=not dry_run)
        existing_ids = self.sheets.get_repasse_existing_ids() if self.sheets is not None and not dry_run else {}
        repasse_html = self.reports.fetch_repasse_html(ano, instituicao_id=instituicao_id)
        repasse_items = self.reports.parse_repasse_html(repasse_html, ano)

        balancete_by_month: Dict[int, List[BalanceteSaidaItem]] = {}
        for month in sorted({item.period.mes for item in repasse_items}):
            period = next(item.period for item in repasse_items if item.period.mes == month)
            html = self.reports.fetch_balancete_html(period, instituicao_id=instituicao_id)
            balancete_by_month[month] = self.reports.parse_balancete_html(html, period)

        records = reconcile_repasse_items(
            repasse_items,
            balancete_by_month,
            instituicao=self.settings.institution_name,
            data_execucao=time.strftime("%d/%m/%Y %H:%M:%S"),
            existing_ids=existing_ids,
        )
        if not dry_run:
            if self.sheets is None:
                raise RuntimeError("Google Sheets não inicializado para gravação em T_REPASSE")
            self.sheets.upsert_repasse_records(records)
        return records


def print_repasse_summary(records: List[RepasseValidationRecord], ano: int) -> None:
    print(f"\nAUDITORIA DE REPASSES — {ano}\n")
    by_month = defaultdict(list)
    for record in records:
        by_month[record.mes].append(record)

    for month, month_records in by_month.items():
        print(month)
        for record in month_records:
            repasse = format_decimal_pt(record.valor_repasse)
            balancete = format_decimal_pt(record.valor_balancete)
            print(
                f"{record.plano_conta_repasse[:32]:32s} "
                f"{repasse:>10s} {balancete:>10s}   "
                f"{record.resultado_validacao}   {record.diagnostico}"
            )
        print()

    total = len(records)
    conferidos = sum(record.resultado_validacao == "CONFERIDO" for record in records)
    incompletos = sum(record.resultado_validacao == "REPASSE INCOMPLETO" for record in records)
    divergencias = sum(
        (record.status_repasse_soma.strip().casefold() == "conferido" and record.resultado_validacao != "CONFERIDO")
        or (record.status_repasse_soma.strip().casefold() != "conferido" and record.resultado_validacao == "CONFERIDO")
        for record in records
    )
    print("RESUMO")
    print(f"Itens analisados: {total}")
    print(f"Conferidos: {conferidos}")
    print(f"Incompletos: {incompletos}")
    print(f"Divergências Status SOMA x Auditoria: {divergencias}")
    print("Diagnósticos:")
    for diagnostico, count in sorted(Counter(record.diagnostico for record in records).items()):
        print(f"  {diagnostico}: {count}")
