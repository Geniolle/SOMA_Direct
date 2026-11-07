from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Adiciona diretório base ao sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from workflows.orchestrator import DirectOrchestrator
from config.paths import PROJECT_ROOT


def setup_agendador_logging(overwrite: bool = True):
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "last_run.log"

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # mode='w' assegura que não guardamos histórico acumulado no disco da VM
    file_handler = logging.FileHandler(log_file, mode="w" if overwrite else "a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = [file_handler, stream_handler]


running = True


def _handle_signal(signum, frame):
    global running
    logging.info(f"Sinal recebido ({signum}). Encerrando agendador graciosamente...")
    running = False


def _get_interval() -> int:
    val = os.getenv("AGENDADOR_INTERVAL_SECONDS", "60").strip()
    try:
        sec = int(val)
        return sec if sec >= 10 else 60
    except ValueError:
        return 60


def run_cycle(orchestrator: DirectOrchestrator, run_maintenance: bool = False) -> None:
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    logging.info(f"--- [CICLO AGENDADO] Iniciando ronda em {now_str} ---")

    try:
        outcomes = orchestrator.run_pending()
        if outcomes:
            sucessos = sum(1 for o in outcomes if o.success)
            falhas = len(outcomes) - sucessos
            logging.info(f"[CICLO AGENDADO] Concluído: {len(outcomes)} itens processados ({sucessos} sucesso, {falhas} falha).")
        else:
            logging.info("[CICLO AGENDADO] Nenhuma linha pendente para processamento no momento.")

        sheets = getattr(orchestrator, "sheets", None)
        backfill_fn = getattr(sheets, "backfill_missing_links", None)
        if callable(backfill_fn):
            backfill_fn()

        if run_maintenance:
            stats = orchestrator.reconcile_scheduled_descriptions()
            logging.info("[MANUTENÇÃO AGENDADA] Resultado: %s", stats)
    except Exception as exc:
        logging.error(f"[CICLO AGENDADO] Erro durante a ronda: {exc}", exc_info=True)


def main():
    global running
    setup_agendador_logging()

    parser = argparse.ArgumentParser(description="Agendador de Produção do SOMA Direct")
    parser.add_argument("--once", action="store_true", help="Executa apenas uma ronda e sai")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logging.info("Iniciando motor SOMA Direct (Agendador de Produção)...")
    orchestrator = DirectOrchestrator()

    if args.once:
        run_cycle(orchestrator, run_maintenance=True)
        return 0

    interval = _get_interval()
    maintenance_interval = max(300, orchestrator.settings.reconciliation_interval_seconds)
    last_maintenance = 0.0
    logging.info(f"Modo contínuo ativo. Intervalo entre rondas: {interval} segundos.")

    while running:
        now = time.monotonic()
        run_maintenance = now - last_maintenance >= maintenance_interval
        run_cycle(orchestrator, run_maintenance=run_maintenance)
        if run_maintenance:
            last_maintenance = time.monotonic()

        logging.info(f"Aguardando {interval}s para a próxima verificação...")
        for _ in range(interval):
            if not running:
                break
            time.sleep(1)

    logging.info("Agendador SOMA Direct finalizado com sucesso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
