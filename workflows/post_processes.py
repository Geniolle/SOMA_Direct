"""Pós-processos executados depois que a fila da CONTAORDEM é esvaziada, portados
do processo legado (SYS_CAIXA.py / process_soma.py em C:\\workspace\\SOMA):

  (6) Caixas/Bancos: lê os saldos atuais no portal SOMA e grava na sheet
      GERENCIAR CAIXAS.
  (7) SOMA: busca os lançamentos dos últimos ~2 meses no portal SOMA e insere
      na sheet SOMA os que ainda não estão lá.

Mesma ordem e mesma trava do legado: (7) só roda se (6) não falhar.
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import datetime
from typing import Tuple

from ronda_completa import load_soma_interval

logger = logging.getLogger("soma_direct.post_processes")


def _intervalo_mes_anterior_ate_mes_atual(now: datetime | None = None) -> Tuple[datetime, datetime]:
    now = now or datetime.now()
    if now.month == 1:
        prev_month, prev_year = 12, now.year - 1
    else:
        prev_month, prev_year = now.month - 1, now.year
    start = datetime(prev_year, prev_month, 1)
    end = datetime(now.year, now.month, monthrange(now.year, now.month)[1])
    return start, end


def atualizar_caixas_bancos(orchestrator) -> int:
    """Passo (6): lê os saldos atuais de Caixas/Bancos e grava na sheet GERENCIAR CAIXAS."""
    saldos = orchestrator.api.buscar_resumo_caixas()
    count = orchestrator.sheets.update_caixas_bancos(saldos)
    logger.info("Post-processo Caixas/Bancos: %d saldo(s) atualizado(s).", count)
    return count


def atualizar_sheet_soma(orchestrator) -> int:
    """Passo (7): busca os lançamentos dos últimos ~2 meses no SOMA e insere os novos na sheet SOMA."""
    start, end = _intervalo_mes_anterior_ate_mes_atual()
    lancamentos = load_soma_interval(orchestrator, start, end)
    count = orchestrator.sheets.append_soma_rows(list(lancamentos.values()))
    logger.info("Post-processo SOMA: %d lançamento(s) novo(s) inserido(s).", count)
    return count


def run_post_processes(
    orchestrator,
    *,
    run_caixas_bancos: bool = True,
    run_soma_sheet: bool = True,
) -> None:
    caixas_ok = True

    if run_caixas_bancos:
        try:
            atualizar_caixas_bancos(orchestrator)
        except Exception:
            caixas_ok = False
            logger.exception("Falha no post-processo Caixas/Bancos.")

    if not caixas_ok:
        logger.warning("Post-processo da sheet SOMA não será executado porque Caixas/Bancos falhou.")
        return

    if run_soma_sheet:
        try:
            atualizar_sheet_soma(orchestrator)
        except Exception:
            logger.exception("Falha no post-processo da sheet SOMA.")
