from __future__ import annotations

import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from domain.models import clean_amount_for_comparison, norm_basic
from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


def main() -> None:
    orchestrator = ReconciliationOrchestrator()
    contaordem_values = orchestrator.sheets._ws.get_all_values()
    conta_headers = {norm_basic(header): index for index, header in enumerate(contaordem_values[0])}
    conta_id_index = conta_headers[norm_basic("ID_INTERNO")]
    existing_ids = {
        row[conta_id_index].strip()
        for row in contaordem_values[1:]
        if conta_id_index < len(row) and row[conta_id_index].strip()
    }

    results = {}
    for source_name in ("DÍZIMOS/OFERTAS", "VC_VENDAS"):
        values = orchestrator._get_source_worksheet(source_name).get_all_values()
        indices = {norm_basic(header): index for index, header in enumerate(values[0])}

        def value(row, field):
            index = indices.get(norm_basic(field))
            return row[index].strip() if index is not None and index < len(row) else ""

        pending = [
            (row_number, row)
            for row_number, row in enumerate(values[1:], start=2)
            if value(row, "ID_INTERNO") and value(row, "ID_INTERNO") not in existing_ids
        ]

        if source_name == "DÍZIMOS/OFERTAS":
            empty = []
            zero = []
            positive = []
            invalid = []
            for row_number, row in pending:
                raw_amount = value(row, "IMPORTÂNCIA") or value(row, "VALOR") or value(row, "MONTANTE")
                if not raw_amount:
                    empty.append(row_number)
                    continue
                try:
                    amount = Decimal(clean_amount_for_comparison(raw_amount).replace(",", "."))
                except (InvalidOperation, ValueError):
                    invalid.append(row_number)
                    continue
                if amount == 0:
                    zero.append(row_number)
                else:
                    positive.append(row_number)
            results[source_name] = {
                "pending": len(pending),
                "empty_amount": len(empty),
                "zero_amount": len(zero),
                "nonzero_amount": len(positive),
                "invalid_amount": len(invalid),
                "empty_rows": empty,
                "zero_rows": zero,
            }
        else:
            payment_methods = Counter(
                norm_basic(value(row, "FORMA DE PAGAMENTO")) or "vazio"
                for _, row in pending
            )
            results[source_name] = {
                "pending": len(pending),
                "payment_methods": dict(payment_methods),
                "cash": payment_methods.get("dinheiro", 0),
                "not_cash": len(pending) - payment_methods.get("dinheiro", 0),
            }

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
