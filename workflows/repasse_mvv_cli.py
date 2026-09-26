from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from services.repasse_mvv_service import format_decimal_pt, parse_decimal
from workflows.repasse_mvv_orchestrator import RepasseMvvOrchestrator, RepassePreview

logger = logging.getLogger("soma_direct.repasse_mvv")


def parse_mes_ano(text: str) -> tuple[int, int]:
    """Valida e extrai mês e ano de uma string 'mm/yyyy' ou 'm/yyyy'."""
    raw = text.strip()
    if "/" not in raw and "-" in raw:
        raw = raw.replace("-", "/")
    parts = raw.split("/")
    if len(parts) != 2:
        raise ValueError("Formato inválido. Utilize mm/yyyy (exemplo: 08/2026).")
    try:
        mes = int(parts[0])
        ano = int(parts[1])
    except ValueError as e:
        raise ValueError("Mês e ano devem ser números inteiros.") from e

    if not 1 <= mes <= 12:
        raise ValueError(f"Mês inválido: {mes}. Deve ser entre 1 e 12.")
    if ano < 2000 or ano > 2100:
        raise ValueError(f"Ano inválido: {ano}.")
    return mes, ano


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repasse MVV - Calcula e submete os 5 repasses institucionais no SOMA."
    )
    parser.add_argument(
        "--mes-ano",
        type=str,
        default=None,
        help="Mês e ano de referência no formato mm/yyyy (ex: 08/2026). Se omitido, pergunta no terminal.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Executa a simulação completa sem gravar dados no SOMA.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Executa a gravação efetiva no SOMA.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Responde 'sim' automaticamente a todas as confirmações.",
    )
    parser.add_argument(
        "--doacoes",
        type=str,
        default=None,
        help="Override manual: total de Doações/Dízimos/Ofertas.",
    )
    parser.add_argument(
        "--lanchonete",
        type=str,
        default=None,
        help="Override manual: total de Receitas de Lanchonete/Café.",
    )
    parser.add_argument(
        "--livraria",
        type=str,
        default=None,
        help="Override manual: total de Receitas de Livraria.",
    )
    parser.add_argument(
        "--total",
        type=str,
        default=None,
        help="Override do valor total do repasse (contrapor valor com rateio proporcional).",
    )
    return parser



def exibir_tabela_preview(preview: RepassePreview, mes: int, ano: int) -> None:
    bases = preview.bases
    print("\n" + "=" * 76)
    print(f"               REPASSE INSTITUCIONAL MVV — {mes:02d}/{ano}")
    print("=" * 76)
    print(f"Origem das Bases: {bases.origem}")
    print(f"  • Doações (Dízimos e Ofertas): {format_decimal_pt(bases.doacoes_dizimos_ofertas):>10} €")
    print(f"  • Receitas de Lanchonete:      {format_decimal_pt(bases.receitas_lanchonete):>10} €")
    print(f"  • Receitas de Livraria:        {format_decimal_pt(bases.receitas_livraria):>10} €")
    print(f"  Total Entradas do Mês:         {format_decimal_pt(bases.total_entrada):>10} €")
    print("-" * 76)
    print(f"{'#':<2} {'Plano de Contas':<22} {'Conta Santander':<14} {'Regra':<23} {'Valor (€)':>10}")
    print("-" * 76)

    for i, it in enumerate(preview.itens, start=1):
        print(
            f"{i:<2} {it.plano_nome:<22} {it.conta_santander_numero:<14} "
            f"{it.aliquota_desc:<23} {format_decimal_pt(it.valor):>10}"
        )

    print("-" * 76)
    print(f"{'TOTAL A REPASSAR:':<63} {format_decimal_pt(preview.total_repasse):>10} €")
    print("=" * 76)

    if preview.existentes_soma:
        print(f"\n[AVISO] Já foram encontrados {len(preview.existentes_soma)} repasse(s) cadastrados no SOMA para {mes:02d}/{ano}:")
        for ex in preview.existentes_soma:
            print(f"   - ID {ex.get('id', '?')}: {ex.get('descricao')} | Valor: {ex.get('valor')} €")
        print("   Verifique antes de prosseguir para não criar lançamentos duplicados.")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 76)
    print("                      PROCESSO REPASSE MVV")
    print("=" * 76)

    # 1. Obter mês e ano
    mes_ano_str = args.mes_ano
    while not mes_ano_str:
        try:
            mes_ano_input = input("\nInforme o mês e ano de referência (mm/yyyy): ").strip()
            if not mes_ano_input:
                print("Operação cancelada pelo utilizador.")
                return 0
            mes_ano_str = mes_ano_input
            parse_mes_ano(mes_ano_str)
        except ValueError as err:
            print(f"[ERRO] {err}")
            mes_ano_str = None

    try:
        mes, ano = parse_mes_ano(mes_ano_str)
    except ValueError as err:
        print(f"[ERRO] Erro nos argumentos: {err}")
        return 1

    orchestrator = RepasseMvvOrchestrator()

    # 2. Obter bases
    doacoes_val = parse_decimal(args.doacoes) if args.doacoes else None
    lanchonete_val = parse_decimal(args.lanchonete) if args.lanchonete else None
    livraria_val = parse_decimal(args.livraria) if args.livraria else None

    try:
        preview = orchestrator.preparar_preview(
            ano=ano,
            mes=mes,
            doacoes_override=doacoes_val,
            lanchonete_override=lanchonete_val,
            livraria_override=livraria_val,
        )
    except ValueError as err:
        print(f"\n[AVISO] {err}")
        # Solicitar valores manualmente
        try:
            print("\nInforme os valores de receita do mês:")
            doa_in = input("  • Doações (Dízimos e Ofertas) [0,00]: ").strip()
            lan_in = input("  • Receitas de Lanchonete/Café [0,00]: ").strip()
            liv_in = input("  • Receitas de Livraria        [0,00]: ").strip()
            preview = orchestrator.preparar_preview(
                ano=ano,
                mes=mes,
                doacoes_override=parse_decimal(doa_in),
                lanchonete_override=parse_decimal(lan_in),
                livraria_override=parse_decimal(liv_in),
            )
        except Exception as exc:
            print(f"[ERRO] Falha ao processar valores manuais: {exc}")
            return 1
    except Exception as exc:
        print(f"[ERRO] Erro de comunicação com o SOMA: {exc}")
        return 1


    # 3. Aplicar override de total se fornecido por argumento
    if args.total:
        try:
            total_override = parse_decimal(args.total)
            from services.repasse_mvv_service import RepasseMvvService
            novos_itens = RepasseMvvService.recalcular_por_novo_total(preview.itens, total_override)
            preview = RepassePreview(
                bases=preview.bases,
                itens=novos_itens,
                total_repasse=total_override,
                existentes_soma=preview.existentes_soma,
            )
        except Exception as e:
            print(f"[ERRO] Falha ao aplicar --total: {e}")
            return 1

    # 4. Loop de Exibição e Confirmação
    dry_run = args.dry_run
    while True:
        exibir_tabela_preview(preview, mes, ano)

        if dry_run:
            print("\nModo: SIMULAÇÃO (Dry-Run ativo — nenhum dado será enviado ao SOMA)")
            if not args.yes:
                conf = input("\nDeseja executar o teste de envio em dry-run? (s/N): ").strip().lower()
                if conf not in ("s", "sim", "y", "yes"):
                    print("\nOperação cancelada.")
                    return 0
            break

        if args.apply or args.yes:
            break

        conf = input("\nDeseja GRAVAR estes 5 repasses no SOMA? (s/N): ").strip().lower()
        if conf in ("s", "sim", "y", "yes"):
            break
        else:
            print("\nOpções:")
            print("  1. Sair (não quero executar)")
            print("  2. Informar outro valor total de repasse (contrapor valor)")
            opcao = input("\nEscolha uma opção [1/2] (padrão 1): ").strip()
            if opcao == "2":
                novo_val_str = input("\nInforme o novo valor total do repasse (€) [ex: 643,74]: ").strip()
                try:
                    novo_total = parse_decimal(novo_val_str)
                    if novo_total <= 0:
                        print("[ERRO] O valor total deve ser maior que zero.")
                        continue
                    from services.repasse_mvv_service import RepasseMvvService
                    novos_itens = RepasseMvvService.recalcular_por_novo_total(preview.itens, novo_total)
                    preview = RepassePreview(
                        bases=preview.bases,
                        itens=novos_itens,
                        total_repasse=novo_total,
                        existentes_soma=preview.existentes_soma,
                    )
                    print(f"\n[OK] Valores recalculados com base no total de {format_decimal_pt(novo_total)} €.")
                    # Continua no loop e reexibe a tabela preview com os novos valores
                except Exception as e:
                    print(f"[ERRO] Valor inválido: {e}")
            else:
                print("\nOperação cancelada. Nenhum repasse foi gravado no SOMA.")
                return 0

    # 5. Executar submissão
    print(f"\nIniciando gravação de {len(preview.itens)} repasses no SOMA...")
    resultados = orchestrator.executar_submissao(
        itens=preview.itens,
        ano=ano,
        mes=mes,
        dry_run=dry_run,
    )


    # 6. Resumo final
    print("\n" + "=" * 76)
    print("                     RESULTADO DA EXECUÇÃO")
    print("=" * 76)
    sucessos = 0
    for res in resultados:
        status_icon = "[OK]" if res.sucesso else "[FALHA]"
        id_info = f"[ID: {res.repasse_id}]" if res.repasse_id else ""
        print(f"{status_icon:<7} {res.item.plano_nome:<24} {format_decimal_pt(res.item.valor):>8} € {id_info} - {res.mensagem}")
        if res.sucesso:
            sucessos += 1

    print("-" * 76)
    print(f"Concluído: {sucessos}/{len(resultados)} repasses processados com sucesso.")
    print("=" * 76)
    return 0 if sucessos == len(resultados) else 1



if __name__ == "__main__":
    raise SystemExit(main())
