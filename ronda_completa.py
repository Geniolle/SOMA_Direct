import argparse
import calendar
import html
import logging
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

base_dir = Path(__file__).resolve().parent
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from config.settings import Settings
from domain.models import (
    TipoMovimento,
    clean_amount_for_comparison,
    clean_caixa,
    extract_suffix_n,
    is_entrada_ou_saida,
    norm_basic,
    normalize_date_str,
    normalize_document_value,
)
from workflows.orchestrator import DirectOrchestrator


@dataclass(frozen=True)
class SomaTransfer:
    transfer_id: str
    caixa_origem: str
    valor_saida: str
    caixa_destino: str
    valor_entrada: str
    data: str
    observacao: str = ""


def progress_text(current: int, total: int) -> str:
    return f"Linhas da sheet no período: {current}/{total}"


def render_progress(current: int, total: int):
    print(f"\r{progress_text(current, total)}", end="", flush=True)


def clear_progress(total: int):
    width = len(progress_text(total, total))
    print(f"\r{' ' * width}\r", end="", flush=True)


def print_row_error(row_number: int, message: str, current: int, total: int):
    clear_progress(total)
    print(f"Linha {row_number}: {message}", flush=True)
    render_progress(current, total)


def parse_date(value: str) -> datetime:
    date_text = value.strip()
    date_format = "%d%m%Y" if date_text.isdigit() and len(date_text) == 8 else "%d/%m/%Y"
    try:
        return datetime.strptime(date_text, date_format)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "use o formato DD/MM/AAAA ou DDMMAAAA"
        ) from exc


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


def is_round_movement(row) -> bool:
    return is_entrada_ou_saida(row.tipo) or row.tipo == TipoMovimento.TRANSFERENCIA


def normalize_transfer_caixa(value) -> str:
    caixa = clean_caixa(value)
    caixa = re.sub(r"\s*-?\s*\b(?:cc|conta corrente)\b\s*$", "", caixa)
    return caixa.strip(" -")


def transfer_key(data, valor, caixa_origem, caixa_destino):
    return (
        normalize_date_str(data),
        clean_amount_for_comparison(valor),
        normalize_transfer_caixa(caixa_origem),
        normalize_transfer_caixa(caixa_destino),
    )


def parse_transfer_table(page_text: str):
    transfers = []
    for raw_row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page_text, re.I | re.S):
        id_match = re.search(
            r'class=["\'][^"\']*\bbnt_excluir\b[^"\']*["\'][^>]*\bid=["\'](\d+)["\']',
            raw_row,
            re.I,
        )
        if not id_match:
            continue
        cells = []
        for raw_cell in re.findall(r"<td\b[^>]*>(.*?)</td>", raw_row, re.I | re.S):
            cell_text = html.unescape(re.sub(r"<[^>]+>", " ", raw_cell))
            cells.append(" ".join(cell_text.split()))
        if len(cells) < 5:
            continue
        origin, amount_out, destination, amount_in, date = cells[-5:]
        obs_match = re.search(
            r'class=["\'][^"\']*\bbtn_obs\b[^"\']*["\'][^>]*\bdata-dados=["\']([^"\']*)["\']',
            raw_row,
            re.I,
        )
        transfers.append(
            SomaTransfer(
                transfer_id=id_match.group(1),
                caixa_origem=origin,
                valor_saida=amount_out,
                caixa_destino=destination,
                valor_entrada=amount_in,
                data=date,
                observacao=html.unescape(obs_match.group(1)) if obs_match else "",
            )
        )
    return transfers


def load_soma_transfers_interval(orchestrator, start_date, end_date):
    url = (
        f"{orchestrator.settings.site_base_url.rstrip('/')}"
        "/sys/post/buscarTransferenciasCaixas.php"
    )
    response = orchestrator.http.post_ajax(
        url,
        data={
            "id_inst": orchestrator.settings.institution_id,
            "i": start_date.strftime("%d/%m/%Y"),
            "f": end_date.strftime("%d/%m/%Y"),
        },
    )
    return parse_transfer_table(response.text)


def delete_soma_transfer(orchestrator, transfer_id: str):
    url = (
        f"{orchestrator.settings.site_base_url.rstrip('/')}"
        "/sys/app/transferencias_caixas.php"
    )
    response = orchestrator.http.post_ajax(
        url,
        data={"id": transfer_id, "excluir": "1"},
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"resposta inválida ao excluir transferência {transfer_id}"
        ) from exc
    if int(payload.get("status", 0)) != 1:
        raise RuntimeError(
            f"SOMA recusou a exclusão da transferência {transfer_id} "
            f"(status={payload.get('status')})"
        )


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


def print_final_result(
    start_date,
    end_date,
    apply_changes,
    stats,
    sheet_rows_count,
    soma_items_count,
    reverse_errors,
):
    direct_divergences = stats["divergentes"]
    reverse_divergences = (
        stats["inversa_duplicada"]
        + stats["inversa_sem_doc"]
        + stats["inversa_ambigua"]
        + stats["inversa_ausente"]
    )
    unresolved_transfer_duplicates = max(
        0,
        stats["transferencias_duplicadas"] - stats["transferencias_removidas"],
    )
    total_divergences = (
        direct_divergences
        + reverse_divergences
        + unresolved_transfer_duplicates
    )
    status = (
        "CONCLUÍDO SEM DIVERGÊNCIAS"
        if total_divergences == 0
        else f"ATENÇÃO: {total_divergences} DIVERGÊNCIA(S) ENCONTRADA(S)"
    )

    print("\n" + "=" * 60)
    print("RESULTADO FINAL DA RONDA")
    print("=" * 60)
    print(f"Status: {status}")
    print(f"Período analisado: {start_date:%d/%m/%Y} a {end_date:%d/%m/%Y}")
    print(
        "Modo de execução: "
        + ("APLICAÇÃO (alterações gravadas)" if apply_changes else "SIMULAÇÃO (nenhuma alteração gravada)")
    )

    print("\n1. Validação direta — Sheet -> SOMA")
    print(f"   Linhas analisadas: {sheet_rows_count}")
    print(f"   Linhas confirmadas: {stats['confirmados']}")
    print(f"   Linhas corrigidas com segurança: {stats['corrigidos']}")
    print(f"   Linhas com divergência: {direct_divergences}")

    print("\n   Transferências")
    print(f"   Registros SOMA analisados: {stats['transferencias_soma_analisadas']}")
    print(f"   Transferências confirmadas: {stats['transferencias_confirmadas']}")
    print(f"   Transferências não encontradas: {stats['transferencias_ausentes']}")
    print(f"   Duplicados idênticos encontrados: {stats['transferencias_duplicadas']}")
    if apply_changes:
        print(f"   Duplicados removidos do SOMA: {stats['transferencias_removidas']}")
    else:
        print("   Duplicados removidos do SOMA: 0 (modo simulação)")

    print("\n2. Validação inversa — SOMA -> Sheet")
    print(f"   Documentos SOMA analisados: {soma_items_count}")
    print(f"   Documentos vinculados corretamente: {stats['inversa_confirmada']}")
    print(f"   Documentos duplicados na Sheet: {stats['inversa_duplicada']}")
    print(f"   Correspondências sem DOC. SOMA: {stats['inversa_sem_doc']}")
    print(f"   Correspondências ambíguas: {stats['inversa_ambigua']}")
    print(f"   Documentos ausentes na Sheet: {stats['inversa_ausente']}")

    if reverse_errors:
        print("\nDetalhes das divergências inversas:")
        for error in reverse_errors:
            print(f"   - {error}")
    else:
        print("\nNenhuma divergência encontrada nas duas validações.")
    print("=" * 60)


def _import_missing_saida_entries(orchestrator) -> dict:
    """Importa SAÍDAS com FINANCE vazio para CONTAORDEM (etapa prévia à ronda)."""
    from collections import defaultdict

    sheets = orchestrator.sheets
    saidas_values = sheets.get_origin_worksheet("SAÍDAS").get_all_values()
    if not saidas_values or len(saidas_values) < 2:
        return {"imported": 0, "total_found": 0}

    saidas_headers = saidas_values[0]
    saidas_indices = {norm_basic(header): idx for idx, header in enumerate(saidas_headers)}

    # Verificar coluna FINANCE
    if norm_basic("FINANCE") not in saidas_indices:
        return {"imported": 0, "total_found": 0, "error": "Coluna FINANCE nao encontrada"}

    co_values = sheets._ws.get_all_values()
    co_headers = co_values[0]
    co_indices = {norm_basic(header): idx for idx, header in enumerate(co_headers)}

    def saida_value(row, field):
        idx = saidas_indices.get(norm_basic(field))
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    def co_value(row, field):
        idx = co_indices.get(norm_basic(field))
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    existing_ids = {co_value(row, "ID_INTERNO") for row in co_values[1:] if co_value(row, "ID_INTERNO")}

    saidas_by_date = defaultdict(list)
    rows_to_import = []

    for saida_row in saidas_values[1:]:
        id_interno = saida_value(saida_row, "ID_INTERNO")
        finance_field = saida_value(saida_row, "FINANCE")

        if not finance_field.strip() and id_interno and id_interno not in existing_ids:
            data_mov = saida_value(saida_row, "DATA")
            saidas_by_date[data_mov].append({
                "saida_row": saida_row,
                "id_interno": id_interno,
                "data_mov": data_mov,
            })
            rows_to_import.append(id_interno)

    if not rows_to_import:
        return {"imported": 0, "total_found": 0}

    months = ("JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
              "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO")

    seq_by_date = defaultdict(int)
    for co_row in co_values[1:]:
        data_mov = co_value(co_row, "DATA MOV.")
        desc_soma = co_value(co_row, "DESCRIÇÃO SOMA")
        seq = extract_suffix_n(desc_soma)
        if seq and data_mov:
            seq_by_date[data_mov] = max(seq_by_date.get(data_mov, 0), seq)

    rows_to_append = []
    for data_mov in sorted(saidas_by_date.keys()):
        items = saidas_by_date[data_mov]
        next_seq = seq_by_date.get(data_mov, 0) + 1

        for item in items:
            saida_row = item["saida_row"]
            id_interno = item["id_interno"]

            descricao = saida_value(saida_row, "DESCRIÇÃO DA COMPRA")
            valor = saida_value(saida_row, "VALOR DA COMPRA")
            tipo = saida_value(saida_row, "TIPO")

            co_row = [""] * len(co_headers)
            mapped = {
                "DATA MOV.": data_mov,
                "DESCRIÇÃO": descricao,
                "IMPORTÂNCIA": clean_amount_for_comparison(valor) if valor else "0,00",
                "TIPO": "Saída" if not tipo or norm_basic(tipo) == "saida" else tipo,
                "PROCESSO": "SAÍDAS",
                "ID_INTERNO": id_interno,
                "DESCRIÇÃO SOMA": f"{descricao} N{next_seq:03d}",
            }

            try:
                parsed_date = datetime.strptime(data_mov, "%d/%m/%Y")
                mapped["PERÍODO"] = months[parsed_date.month - 1]
            except (ValueError, IndexError):
                pass

            for field, field_value in mapped.items():
                field_idx = co_indices.get(norm_basic(field))
                if field_idx is not None:
                    co_row[field_idx] = field_value

            rows_to_append.append(co_row)
            next_seq += 1
            seq_by_date[data_mov] = next_seq - 1

    if rows_to_append:
        sheets._ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")

    return {"imported": len(rows_to_append), "total_found": len(rows_to_import)}


def run_round(start_date, end_date, apply_changes=False):
    orchestrator = DirectOrchestrator(Settings.from_env())
    orchestrator.auth.login()

    # Etapa prévia: importar SAÍDAS com FINANCE vazio para CONTAORDEM
    logger = logging.getLogger("soma_direct.ronda")
    if apply_changes:
        try:
            logger.info("Importando SAÍDAS com FINANCE vazio para CONTAORDEM...")
            import_saidas_result = _import_missing_saida_entries(orchestrator)
            logger.info(f"Importacao: {import_saidas_result['imported']} linha(s) criada(s)")
        except Exception as e:
            logger.warning(f"Falha na importacao de SAÍDAS: {e}")

    all_rows = orchestrator.sheets.get_all_rows(only_entrada_saida=False)
    rows = [
        row for row in all_rows
        if is_round_movement(row)
        and row_date_in_interval(row.data_mov, start_date, end_date)
    ]
    stats = Counter()
    updates = []
    audit_logger = logging.getLogger("soma_direct.audit")
    previous_audit_level = audit_logger.level
    audit_logger.setLevel(logging.ERROR)

    soma_transfers = load_soma_transfers_interval(orchestrator, start_date, end_date)
    stats["transferencias_soma_analisadas"] = len(soma_transfers)
    transfers_by_key = defaultdict(list)
    for transfer in soma_transfers:
        key = transfer_key(
            transfer.data,
            transfer.valor_saida,
            transfer.caixa_origem,
            transfer.caixa_destino,
        )
        if (
            clean_amount_for_comparison(transfer.valor_saida)
            == clean_amount_for_comparison(transfer.valor_entrada)
        ):
            transfers_by_key[key].append(transfer)
    processed_transfer_keys = set()

    for index, row in enumerate(rows, start=1):
        if row.tipo == TipoMovimento.TRANSFERENCIA:
            key = transfer_key(
                row.data_mov,
                row.importancia,
                row.caixa_saida,
                row.caixa,
            )
            matches = sorted(
                transfers_by_key.get(key, []),
                key=lambda transfer: int(transfer.transfer_id),
            )
            if not matches:
                stats["divergentes"] += 1
                stats["transferencias_ausentes"] += 1
                audit_text = (
                    "Transferência não encontrada no SOMA com a mesma data, "
                    "valor, caixa de saída e caixa de destino"
                )
            else:
                stats["confirmados"] += 1
                stats["transferencias_confirmadas"] += 1
                audit_text = "Confirmado"
                if key not in processed_transfer_keys and len(matches) > 1:
                    duplicates = matches[1:]
                    stats["transferencias_duplicadas"] += len(duplicates)
                    if apply_changes:
                        for duplicate in duplicates:
                            delete_soma_transfer(orchestrator, duplicate.transfer_id)
                            stats["transferencias_removidas"] += 1
                processed_transfer_keys.add(key)
            updates.append({"row_idx": row.row_number, "auditoria": audit_text})
            if apply_changes and len(updates) >= 25:
                orchestrator.sheets.batch_update_audit_records(updates)
                updates.clear()
            if audit_text != "Confirmado":
                print_row_error(row.row_number, audit_text, index, len(rows))
            else:
                render_progress(index, len(rows))
            continue

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
                if outcome.corrected:
                    stats["corrigidos"] += 1
            else:
                audit_text = (
                    "; ".join(outcome.inconsistencies)
                    or "Registo não confirmado no SOMA"
                )
                stats["divergentes"] += 1
                if outcome.inconsistent and (
                    "DADOS DOC" in audit_text
                    or "CAIXA" in audit_text.upper()
                    or "FORMA DE PAGAMENTO" in audit_text.upper()
                ):
                    audit_text = f"Falha Caixa/Forma em DADOS DOC ({audit_text})"
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
        if audit_text != "Confirmado":
            print_row_error(row.row_number, audit_text, index, len(rows))
        else:
            render_progress(index, len(rows))

    if rows:
        print()
    audit_logger.setLevel(previous_audit_level)

    if apply_changes and updates:
        orchestrator.sheets.batch_update_audit_records(updates)

    # Quinta validação: documentos do SOMA que não ficaram ligados à sheet.
    if apply_changes:
        all_rows = orchestrator.sheets.get_all_rows(only_entrada_saida=False)
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

    print_final_result(
        start_date=start_date,
        end_date=end_date,
        apply_changes=apply_changes,
        stats=stats,
        sheet_rows_count=len(rows),
        soma_items_count=len(soma_items),
        reverse_errors=reverse_errors,
    )
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
