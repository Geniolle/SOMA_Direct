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
    parser.add_argument("rows", nargs="*", type=int, help="Números de linhas pontuais para processar ou auditar")
    parser.add_argument("--mode", choices=["launch", "audit"], default="launch", help="Modo de operação: launch (lançamento) ou audit (auditoria)")
    parser.add_argument("--audit", action="store_true", help="Atalho para --mode audit")
    parser.add_argument("--pending", action="store_true", help="Processa todas as linhas pendentes da planilha CONTAORDEM")
    parser.add_argument("--revalidate-inconsistent", action="store_true", help="Revalida linhas com status Inconsistente e grava o motivo detalhado do erro")
    parser.add_argument("--limit", type=int, default=0, help="Limite de linhas a processar/auditar")
    parser.add_argument("--dry-run", action="store_true", help="Executa no modo de simulação (sem gravar no SOMA nem na planilha)")

    args = parser.parse_args()
    mode = "audit" if args.audit else args.mode

    print("=" * 75)
    print(">>> SOMA DIRECT (MOTOR HTTP MODULAR E DE ALTA VELOCIDADE) <<<")
    print("=" * 75)

    orchestrator = DirectOrchestrator()
    t0 = time.perf_counter()

    if args.revalidate_inconsistent:
        print(f"Modo: REVALIDAÇÃO DETALHADA DE INCONSISTÊNCIAS (DryRun={args.dry_run})\n")
        orchestrator.revalidate_inconsistent(dry_run=args.dry_run)
        return

    if mode == "audit":

        print(f"Modo: AUDITORIA E CONCILIAÇÃO (DryRun={args.dry_run})\n")
        if args.rows:
            print(f"Linhas alvo: {args.rows}")
            outcomes = orchestrator.audit_target_rows(args.rows, dry_run=args.dry_run)
            total_time = time.perf_counter() - t0
            print("\n" + "=" * 75)
            print(f"RESULTADOS DA AUDITORIA (Tempo Total: {total_time:.2f}s):")
            for idx, o in zip(args.rows, outcomes):
                status_txt = "CONFIRMADO" if o.confirmed else ("CORRIGIDO" if o.corrected else "INCONSISTENTE")
                extra = f" -> Novo DOC={o.new_doc}" if o.corrected else ""
                print(f"  Linha {idx:4d}: Resultado={status_txt:14s}{extra}")
            print("=" * 75)
        else:
            orchestrator.audit_pending(limit=args.limit, dry_run=args.dry_run)
        return

    # Modo: Lançamento
    if args.pending:
        print(f"Modo: PROCESSAMENTO DE PENDENTES (Limite={args.limit or 'Sem limite'}, DryRun={args.dry_run})\n")
        outcomes = orchestrator.run_pending(limit=args.limit, dry_run=args.dry_run)
    elif args.rows:
        print(f"Modo: LINHAS ESPECIFICADAS -> {args.rows} (DryRun={args.dry_run})\n")
        outcomes = orchestrator.run_target_rows(args.rows, dry_run=args.dry_run)
    else:
        # Padrão: sem linhas/flag informadas, processa os pendentes reais da planilha
        print(f"Modo padrão: PROCESSAMENTO DE PENDENTES (Limite={args.limit or 'Sem limite'}, DryRun={args.dry_run})\n")
        outcomes = orchestrator.run_pending(limit=args.limit, dry_run=args.dry_run)

    total_time = time.perf_counter() - t0

    print("\n" + "=" * 75)
    print(f"RESULTADOS DA EXECUÇÃO (Tempo Total: {total_time:.2f}s):")
    for o in outcomes:
        status_txt = "OK" if o.success else "FALHA"
        print(f"  Linha {o.row_number:4d} ({o.tipo:13s}): DOC={o.doc_id:15s} | Status={status_txt:5s} | Tempo={o.elapsed_ms}ms ({o.elapsed_ms/1000:.2f}s)")
    print("=" * 75)


if __name__ == "__main__":
    main()

