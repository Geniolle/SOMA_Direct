import argparse
import calendar
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

base_dir = Path(__file__).resolve().parent
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from domain.models import (
    clean_amount_for_comparison,
    is_entrada_ou_saida,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
)
from workflows.orchestrator import DirectOrchestrator


def parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use o formato DD/MM/AAAA") from exc


def resolve_interval(start: str = "", end: str = ""):
    start_text = start.strip() if start else input("Data inicial (DD/MM/AAAA): ").strip()
    end_text = end.strip() if end else input("Data final (DD/MM/AAAA): ").strip()
    start_date = parse_date(start_text)
    end_date = parse_date(end_text)
    if start_date > end_date:
        raise ValueError("A data inicial não pode ser posterior à data final")
    return start_date, end_date


def row_date_in_interval(value, start_date, end_date):
    try:
        parsed = parse_date(normalize_date_str(value))
    except argparse.ArgumentTypeError:
        return False
    return start_date <= parsed <= end_date


def load_soma_interval(orchestrator, start_date, end_date):
    results = {}
    cursor = datetime(start_date.year, start_date.month, 1)
    while cursor <= end_date:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        month_start = max(start_date, cursor)
        month_end = min(end_date, datetime(cursor.year, cursor.month, last_day))
        for date_type in ("1", "0"):
            payload = {
                "pesquisa": "",
                "filtro": "descricao",
                "id_inst": orchestrator.settings.institution_id,
                "tipo": "2",
                "v": "1",
                "s": "2",
                "t_d": date_type,
                "cc": "-1",
                "c": "",
                "i": month_start.strftime("%d/%m/%Y"),
                "f": month_end.strftime("%d/%m/%Y"),
            }
            url = (
                f"{orchestrator.settings.site_base_url.rstrip('/')}"
                "/sys/post/buscarEntradasSaidas.php"
            )
            response = orchestrator.http.post_ajax(url, data=payload)
            for item in orchestrator.audit_service._parse_search_table(response.text):
                doc = normalize_document_value(item.codigo)
                if doc and row_date_in_interval(item.data, start_date, end_date):
                    results[doc] = item
        if cursor.month == 12:
            cursor = datetime(cursor.year + 1, 1, 1)
        else:
            cursor = datetime(cursor.year, cursor.month + 1, 1)
    return results


def run_round(start_date, end_date, apply_changes=False):
    orchestrator = DirectOrchestrator(Settings.from_env())
    orchestrator.auth.login()
    all_rows = orchestrator.sheets.get_all_rows(only_entrada_saida=True)
    rows = [
        row for row in all_rows
        if is_entrada_ou_saida(row.tipo)
        and row_date_in_interval(row.data_mov, start_date, end_date)
    ]
    stats = Counter()
    updates = []
    print(f"Linhas da sheet no período: {len(rows)}", flush=True)

    for index, row in enumerate(rows, start=1):
        validation_error = orchestrator._validate_launch_row(row)
        if validation_error:
            audit_text = validation_error
            stats["divergentes"] += 1
            update = {"row_idx": row.row_number, "auditoria": audit_text}
        else:
            outcome = orchestrator.audit_service.audit_row(
                row,
                allow_soma_mutation=apply_changes,
            )
            if outcome.confirmed or outcome.corrected:
                audit_text = "Confirmado"
                stats["confirmados"] += 1
            else:
                audit_text = (
                    "; ".join(outcome.inconsistencies)
                    or "Registo não confirmado no SOMA"
                )
                stats["divergentes"] += 1
            update = {
                "row_idx": row.row_number,
                "auditoria": audit_text,
                "new_doc": outcome.new_doc,
                "new_desc": outcome.new_desc,
                "dados_doc": (
                    outcome.dados_doc
                    if outcome.dados_doc and outcome.dados_doc != row.dados_doc
                    else None
                ),
            }
        updates.append(update)
        if apply_changes and len(updates) >= 25:
            orchestrator.sheets.batch_update_audit_records(updates)
            updates.clear()
        if index % 25 == 0 or index == len(rows):
            print(f"Validação direta: {index}/{len(rows)}", flush=True)

    if apply_changes and updates:
        orchestrator.sheets.batch_update_audit_records(updates)

    # Quinta validação: documentos do SOMA que não ficaram ligados à sheet.
    if apply_changes:
        all_rows = orchestrator.sheets.get_all_rows(only_entrada_saida=True)
    soma_items = load_soma_interval(orchestrator, start_date, end_date)
    sheet_by_doc = defaultdict(list)
    for row in all_rows:
        doc = normalize_document_value(row.doc_soma)
        if doc.isdigit():
            sheet_by_doc[doc].append(row)

    reverse_errors = []
    for doc, item in soma_items.items():
        linked = sheet_by_doc.get(doc, [])
        if len(linked) == 1:
            stats["inversa_confirmada"] += 1
            continue
        if len(linked) > 1:
            stats["inversa_duplicada"] += 1
            reverse_errors.append(
                f"DOC {doc}: duplicado nas linhas "
                + ",".join(str(row.row_number) for row in linked)
            )
            continue
        exact = [
            row for row in all_rows
            if normalize_date_str(row.data_mov) == normalize_date_str(item.data)
            and norm_basic(row.tipo.value) == norm_basic(item.tipo)
            and clean_amount_for_comparison(row.importancia)
            == clean_amount_for_comparison(item.valor)
            and norm_basic(row.descricao_soma or row.descricao)
            == norm_basic(item.descricao)
        ]
        if len(exact) == 1:
            stats["inversa_sem_doc"] += 1
            reverse_errors.append(
                f"DOC {doc}: correspondência na linha {exact[0].row_number}, "
                f"mas DOC. SOMA='{exact[0].doc_soma}'"
            )
        elif len(exact) > 1:
            stats["inversa_ambigua"] += 1
            reverse_errors.append(
                f"DOC {doc}: {len(exact)} correspondências exatas na sheet"
            )
        else:
            stats["inversa_ausente"] += 1
            reverse_errors.append(
                f"DOC {doc}: ausente na sheet | {item.data} | "
                f"{item.tipo} | {item.valor} | {item.descricao}"
            )

    print("\nRESULTADO FINAL")
    print(f"Período: {start_date:%d/%m/%Y} a {end_date:%d/%m/%Y}")
    print(f"Modo: {'APLICAÇÃO' if apply_changes else 'SIMULAÇÃO'}")
    print(f"Estatísticas: {dict(stats)}")
    print(f"Documentos SOMA na análise inversa: {len(soma_items)}")
    for error in reverse_errors:
        print(f"DIVERGÊNCIA INVERSA: {error}")
    return dict(stats)


def main():
    parser = argparse.ArgumentParser(
        description="Ronda completa SOMA x CONTAORDEM por intervalo de datas"
    )
    parser.add_argument("--inicio", help="Data inicial DD/MM/AAAA")
    parser.add_argument("--fim", help="Data final DD/MM/AAAA")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Grava resultados e correções seguras")
    mode.add_argument("--simulation", action="store_true", help="Somente leitura")
    args = parser.parse_args()

    try:
        start_date, end_date = resolve_interval(args.inicio or "", args.fim or "")
        apply_changes = args.apply
        if not args.apply and not args.simulation:
            answer = input("Aplicar correções seguras? [s/N]: ").strip().lower()
            apply_changes = answer in ("s", "sim")
        run_round(start_date, end_date, apply_changes=apply_changes)
    except (ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
