import argparse
import logging
import sys
import time
from pathlib import Path

# Adiciona diretório raiz ao sys.path
base_dir = Path(__file__).resolve().parent
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from workflows.orchestrator import DirectOrchestrator
from config.settings import Settings


def setup_logging():
    log_dir = base_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    today_str = time.strftime("%Y%m%d")
    log_file = log_dir / f"soma_direct_{today_str}.log"

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = [file_handler, stream_handler]


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="SOMA Direct - Motor de Alta Velocidade para o Portal SOMA")
    parser.add_argument("rows", nargs="*", type=int, help="Números de linhas pontuais para processar")
    parser.add_argument("--pending", action="store_true", help="Processa todas as linhas pendentes da planilha CONTAORDEM")
    parser.add_argument("--limit", type=int, default=0, help="Limite de linhas a processar (quando usado com --pending)")
    parser.add_argument("--dry-run", action="store_true", help="Executa no modo de simulação (sem gravar no SOMA nem na planilha)")

    args = parser.parse_args()

    print("=" * 75)
    print(">>> SOMA DIRECT (MOTOR HTTP MODULAR E DE ALTA VELOCIDADE) <<<")
    print("=" * 75)

    orchestrator = DirectOrchestrator()
    t0 = time.perf_counter()

    if args.pending:
        print(f"Modo: PROCESSAMENTO DE PENDENTES (Limite={args.limit or 'Sem limite'}, DryRun={args.dry_run})\n")
        outcomes = orchestrator.run_pending(limit=args.limit, dry_run=args.dry_run)
    elif args.rows:
        print(f"Modo: LINHAS ESPECIFICADAS -> {args.rows} (DryRun={args.dry_run})\n")
        outcomes = orchestrator.run_target_rows(args.rows, dry_run=args.dry_run)
    else:
        # Padrão: processa as 3 linhas de teste
        default_rows = [4248, 4246, 4247]
        print(f"Modo padrão: LINHAS DE TESTE -> {default_rows} (DryRun={args.dry_run})\n")
        outcomes = orchestrator.run_target_rows(default_rows, dry_run=args.dry_run)

    total_time = time.perf_counter() - t0

    print("\n" + "=" * 75)
    print(f"RESULTADOS DA EXECUÇÃO (Tempo Total: {total_time:.2f}s):")
    for o in outcomes:
        status_txt = "OK" if o.success else "FALHA"
        print(f"  Linha {o.row_number:4d} ({o.tipo:13s}): DOC={o.doc_id:15s} | Status={status_txt:5s} | Tempo={o.elapsed_ms}ms ({o.elapsed_ms/1000:.2f}s)")
    print("=" * 75)


if __name__ == "__main__":
    main()
