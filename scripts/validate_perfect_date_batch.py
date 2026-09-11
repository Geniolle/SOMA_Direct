from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from core.auth import SomaAuthenticator
from core.http_session import ResilientSession
from domain.models import clean_amount_for_comparison, norm_basic, normalize_date_str
from services.audit_service import AuditService
from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


SOURCE_CONFIG = {
    "t_extrato": ("T_EXTRATO", "DATA MOV.", "IMPORTÂNCIA", None),
    "dizimos/ofertas": ("DÍZIMOS/OFERTAS", "DATA", "VALOR", "nonzero"),
    "saidas": ("SAÍDAS", "DATA", "VALOR DA COMPRA", None),
    "financeiro": ("Financeiro", "DATA", "MONTANTE", None),
    "vc_vendas": ("VC_VENDAS", "DATA", "VALOR A PAGAR", "cash"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only-current-errors", action="store_true")
    parser.add_argument("--cache-file")
    parser.add_argument("--auto-delete-extras", action="store_true")
    args = parser.parse_args()
    target_date = normalize_date_str(args.date)

    orchestrator = ReconciliationOrchestrator()
    cache = {}
    if args.cache_file:
        cache = json.loads(Path(args.cache_file).read_text(encoding="utf-8"))
    conta_values = cache.get("CONTAORDEM") or orchestrator.sheets._ws.get_all_values()
    conta_indices = {norm_basic(header): index for index, header in enumerate(conta_values[0])}

    def cell(row, indices, field):
        index = indices.get(norm_basic(field))
        return row[index].strip() if index is not None and index < len(row) else ""

    conta_rows = []
    for row_number, row in enumerate(conta_values[1:], start=2):
        if normalize_date_str(cell(row, conta_indices, "DATA MOV.")) != target_date:
            continue
        conta_rows.append((row_number, row))

    soma_conta_rows = [
        (row_number, row)
        for row_number, row in conta_rows
        if norm_basic(cell(row, conta_indices, "TIPO")) in ("entrada", "saida")
    ]

    conta_by_process = defaultdict(list)
    for row_number, row in conta_rows:
        conta_by_process[norm_basic(cell(row, conta_indices, "PROCESSO"))].append((row_number, row))

    errors = []
    for process, config in SOURCE_CONFIG.items():
        sheet_name, date_field, amount_field, eligibility = config
        source_values = cache.get(sheet_name) or orchestrator._get_source_worksheet(sheet_name).get_all_values()
        source_indices = {norm_basic(header): index for index, header in enumerate(source_values[0])}
        source_rows = []
        for row_number, row in enumerate(source_values[1:], start=2):
            if normalize_date_str(cell(row, source_indices, date_field)) != target_date:
                continue
            if not cell(row, source_indices, "ID_INTERNO"):
                continue
            if eligibility == "cash" and norm_basic(cell(row, source_indices, "FORMA DE PAGAMENTO")) != "dinheiro":
                continue
            amount = clean_amount_for_comparison(cell(row, source_indices, amount_field))
            if eligibility == "nonzero":
                if not amount:
                    continue
                try:
                    if Decimal(amount.replace(",", ".")) == 0:
                        continue
                except Exception:
                    continue
            source_rows.append((row_number, row))

        target_rows = conta_by_process.get(process, [])
        if len(source_rows) != len(target_rows):
            errors.append(f"Quantidade {sheet_name}: CONTAORDEM={len(target_rows)} / ORIGEM={len(source_rows)}")
            continue
        source_by_id = {
            cell(row, source_indices, "ID_INTERNO"): clean_amount_for_comparison(cell(row, source_indices, amount_field))
            for _, row in source_rows
        }
        for row_number, row in target_rows:
            id_interno = cell(row, conta_indices, "ID_INTERNO")
            conta_amount = clean_amount_for_comparison(cell(row, conta_indices, "IMPORTÂNCIA"))
            if id_interno not in source_by_id:
                errors.append(f"Linha {row_number}: ID_INTERNO {id_interno} ausente em {sheet_name}")
            elif source_by_id[id_interno].lstrip("-") != conta_amount.lstrip("-"):
                errors.append(f"Linha {row_number}: valor origem={source_by_id[id_interno]} / CONTAORDEM={conta_amount}")

    unknown = [process for process in conta_by_process if process not in SOURCE_CONFIG]
    if unknown:
        errors.append(f"Origem não configurada: {', '.join(unknown)}")

    if errors:
        result = "Erro: " + " | ".join(errors)
    else:
        settings = Settings.from_env()
        http = ResilientSession(timeout=settings.timeout_seconds)
        if not SomaAuthenticator(settings, http).login():
            raise RuntimeError("Falha no login do SOMA")
        audit = AuditService(settings, http, orchestrator.sheets)
        soma_items = audit.search_by_periodo(target_date)
        soma_valid_items = [
            item for item in soma_items
            if norm_basic(item.tipo) in ("entrada", "saida")
        ]
        soma_counter = Counter(
            (norm_basic(item.tipo), norm_basic(item.descricao), clean_amount_for_comparison(item.valor))
            for item in soma_valid_items
        )
        conta_counter = Counter(
            (
                norm_basic(cell(row, conta_indices, "TIPO")),
                norm_basic(cell(row, conta_indices, "DESCRIÇÃO SOMA")),
                clean_amount_for_comparison(cell(row, conta_indices, "IMPORTÂNCIA")),
            )
            for _, row in soma_conta_rows
        )
        if conta_counter == soma_counter:
            result = "Perfeito"
        else:
            missing = list((conta_counter - soma_counter).elements())
            extra = list((soma_counter - conta_counter).elements())
            extra_details = []
            extra_items = []
            remaining_extra = Counter(extra)
            for item in soma_valid_items:
                key = (norm_basic(item.tipo), norm_basic(item.descricao), clean_amount_for_comparison(item.valor))
                if remaining_extra[key] <= 0:
                    continue
                remaining_extra[key] -= 1
                extra_items.append(item)
                extra_details.append(
                    f"DOC {item.codigo} status={item.status or 'vazio'} baixa={item.baixa or 'vazio'}"
                )
            if args.auto_delete_extras and extra_items:
                delete_url = f"{settings.site_base_url.rstrip('/')}/sys/app/entradas_saidas.php"
                for item in extra_items:
                    delete_response = http.post_ajax(delete_url, data={"id": item.codigo, "excluir": "1"})
                    try:
                        delete_result = delete_response.json()
                    except Exception as error:
                        raise RuntimeError(f"Resposta inválida ao excluir DOC {item.codigo}") from error
                    if int(delete_result.get("status", 0)) != 1:
                        raise RuntimeError(f"Falha ao excluir DOC {item.codigo}: {delete_result}")
                    print(f"Extra excluído automaticamente: DOC={item.codigo}")
                result = "Perfeito" if not missing else f"Erro SOMA: ausentes={len(missing)} / extras=0"
            else:
                detail = f" | extras: {', '.join(extra_details)}" if extra_details else ""
                result = f"Erro SOMA: ausentes={len(missing)} / extras={len(extra)}{detail}"

    print(f"Data={target_date} linhas={len(conta_rows)} resultado={result}")
    update_rows = [
        (row_number, row)
        for row_number, row in conta_rows
        if not args.only_current_errors or cell(row, conta_indices, "AUDITORIA").startswith("Erro")
    ]
    if args.apply and update_rows:
        orchestrator.sheets.batch_update_audit_records([
            {"row_idx": row_number, "auditoria": result}
            for row_number, _ in update_rows
        ])
        print(f"Atualizações aplicadas: {len(update_rows)}")
    else:
        print("Simulação: nenhuma atualização aplicada.")


if __name__ == "__main__":
    main()
