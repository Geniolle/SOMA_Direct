from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from workflows.monthly_checklist_orchestrator import (
    MonthlyChecklistOrchestrator,
    print_monthly_checklist_report,
    print_origin_validation_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Checklist mensal CONTAORDEM x Balancete x Fluxo de Caixa.")
    parser.add_argument("--ano", type=int, help="Ano a validar (ex: 2024). Se omitido junto com --mes, processa todos os períodos pendentes em loop.")
    parser.add_argument("--mes", type=int, help="Mês a validar (ex: 1). Se omitido junto com --ano, processa todos os períodos pendentes em loop.")
    parser.add_argument("--primeiro-periodo", action="store_true", help="Apenas descobre e exibe o primeiro período pendente de auditoria.")
    parser.add_argument("--dry-run", action="store_true", help="Executa em modo de simulação (somente leitura), sem gravar na folha CONTAORDEM.")
    parser.add_argument("--apply", action="store_true", default=True, help="Grava na folha CONTAORDEM.AUDITORIA (comportamento padrão).")
    parser.add_argument("--forcar-todos", action="store_true", help="Valida todas as linhas do mês, inclusive as que já têm AUDITORIA preenchida.")
    parser.add_argument("--apenas-conferidos", action="store_true", help="Grava em CONTAORDEM.AUDITORIA apenas os registos validados como CONFERIDO.")
    parser.add_argument("--max-periodos", type=int, default=None, help="Número máximo de períodos a processar no modo contínuo.")
    parser.add_argument("--validar-origem", action="store_true", help="Valida DESCRIÇÃO SOMA e origem por PROCESSO/ID_INTERNO, gravando o resultado na coluna ORIGEM quando em modo apply.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    orchestrator = MonthlyChecklistOrchestrator()

    if args.primeiro_periodo:
        period, count = orchestrator.discover_first_period()
        if period is None:
            print("Nenhum registo pendente com campo AUDITORIA vazio encontrado.")
            return 1
        print(f"Primeira DATA MOV. pendente: {period.data_inicio}")
        print(f"Primeiro período pendente: {period.label}")
        print(f"Registos CONTAORDEM com AUDITORIA vazia: {count}")
        return 0

    ano = args.ano
    mes = args.mes
    if (ano is None) ^ (mes is None):
        raise SystemExit("Se especificar o período manualmente, forneça ambos --ano e --mes.")

    apply = not args.dry_run

    if not apply:
        print()
        print("!" * 70)
        print("AVISO: Executando em MODO DRY-RUN (SOMENTE LEITURA - SIMULAÇÃO).")
        print("Nenhuma alteração será gravada na folha CONTAORDEM.")
        print("!" * 70)
        print()
    else:
        print()
        print("*" * 70)
        print("MODO GRAVAÇÃO ATIVO.")
        print("Os resultados validados serão gravados na coluna AUDITORIA da CONTAORDEM.")
        print("*" * 70)
        print()

    only_empty = not args.forcar_todos

    if args.validar_origem:
        result = orchestrator.validate_origin_column(
            ano=ano,
            mes=mes,
            apply=apply,
        )
        print_origin_validation_report(result)
        return 0

    if ano is not None and mes is not None:
        result = orchestrator.run(
            ano,
            mes,
            apply=apply,
            only_empty_auditoria=only_empty,
            only_conferido=args.apenas_conferidos,
        )
        print_monthly_checklist_report(result)
        return 0

    results = orchestrator.run_until_complete(
        apply=apply,
        only_empty_auditoria=only_empty,
        max_periods=args.max_periodos,
        only_conferido=args.apenas_conferidos,
    )
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
