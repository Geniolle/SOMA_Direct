from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from domain.models import clean_amount_for_comparison, norm_basic, normalize_date_str
from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


MONTHS = (
    "JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
    "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO",
)
DESCRIPTION_BASE = "PAGAMENTO FORNECEDOR (VERBO CAFÉ)"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    orchestrator = ReconciliationOrchestrator()
    source_sheet = orchestrator._get_source_worksheet("Financeiro")
    source_values = source_sheet.get_all_values()
    target_values = orchestrator.sheets._ws.get_all_values()
    source_indices = {norm_basic(header): index for index, header in enumerate(source_values[0])}
    target_indices = {norm_basic(header): index for index, header in enumerate(target_values[0])}

    def value(row, indices, field):
        index = indices[norm_basic(field)]
        return row[index].strip() if index < len(row) else ""

    existing_ids = {
        value(row, target_indices, "ID_INTERNO")
        for row in target_values[1:]
        if value(row, target_indices, "ID_INTERNO")
    }
    sequences = defaultdict(set)
    for row in target_values[1:]:
        if norm_basic(value(row, target_indices, "PROCESSO")) != "financeiro":
            continue
        description = value(row, target_indices, "DESCRIÇÃO SOMA")
        match = re.search(r"\bN(\d+)\b", description, re.IGNORECASE)
        if match:
            sequences[normalize_date_str(value(row, target_indices, "DATA MOV."))].add(int(match.group(1)))

    rows_to_append = []
    selected_ids = set()
    for source_row in source_values[1:]:
        id_interno = value(source_row, source_indices, "ID_INTERNO")
        if not id_interno or id_interno in existing_ids:
            continue
        if id_interno in selected_ids:
            raise RuntimeError(f"ID duplicado na origem Financeiro: {id_interno}")
        selected_ids.add(id_interno)
        data_mov = normalize_date_str(value(source_row, source_indices, "DATA"))
        used_sequences = sequences[data_mov]
        sequence = 1
        while sequence in used_sequences:
            sequence += 1
        used_sequences.add(sequence)
        amount = abs(Decimal(clean_amount_for_comparison(value(source_row, source_indices, "MONTANTE")).replace(",", ".")))
        tipo_origem = value(source_row, source_indices, "TIPO")
        mapped = {
            "DATA MOV.": data_mov,
            "DESCRIÇÃO": f"{tipo_origem} {id_interno}".strip(),
            "IMPORTÂNCIA": f"{amount:.2f}".replace(".", ","),
            "TIPO": "Saída",
            "DOC. SOMA": "Analisar",
            "PLANO DE CONTA": "FORNECEDORES LANCHONETE",
            "CENTRO DE CUSTO": "10.10.05 - VERBO CAFE",
            "DESCRIÇÃO SOMA": f"{DESCRIPTION_BASE} N{sequence:03d}",
            "FORMA DE PAGAMENTO": "DINHEIRO",
            "CAIXA": "VERBO CAFÉ",
            "PERÍODO": MONTHS[datetime.strptime(data_mov, "%d/%m/%Y").month - 1],
            "PROCESSO": "FINANCEIRO",
            "ID_INTERNO": id_interno,
        }
        target_row = [""] * len(target_values[0])
        for field, field_value in mapped.items():
            target_row[target_indices[norm_basic(field)]] = field_value
        rows_to_append.append(target_row)
        print(f"Preparado: {id_interno} | {data_mov} | {mapped['IMPORTÂNCIA']} | {mapped['DESCRIÇÃO SOMA']}")

    print(f"Total preparado: {len(rows_to_append)}")
    if args.apply and rows_to_append:
        orchestrator.sheets._ws.append_rows(rows_to_append, value_input_option="USER_ENTERED")
        print(f"Total criado: {len(rows_to_append)}")
    else:
        print("Simulação: nenhuma linha criada.")


if __name__ == "__main__":
    main()
