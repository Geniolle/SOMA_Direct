from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from domain.models import is_entrada_ou_saida, normalize_date_str
from services.sheets_service import GoogleSheetsService
from workflows.reconciliation_orchestrator import ReconciliationOrchestrator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recheck-errors", action="store_true")
    parser.add_argument("--only-blank", action="store_true")
    args = parser.parse_args()
    sheets = GoogleSheetsService(Settings.from_env())
    rows = sheets.get_all_rows(only_entrada_saida=not args.recheck_errors)
    completed_prefixes = ("Perfeito", "Erro SOMA:", "Erro: Quantidade", "Erro: Linha", "Erro: Origem")
    dates = sorted(
        {
            normalize_date_str(row.data_mov)
            for row in rows
            if normalize_date_str(row.data_mov)
            and (
                row.auditoria.startswith("Erro")
                if args.recheck_errors
                else is_entrada_ou_saida(row.tipo)
                and (
                    not row.auditoria.strip()
                    if args.only_blank
                    else not row.auditoria.startswith(completed_prefixes)
                )
            )
        },
        key=lambda value: datetime.strptime(value, "%d/%m/%Y"),
        reverse=True,
    )
    print(f"Lotes pendentes: {len(dates)}", flush=True)
    orchestrator = ReconciliationOrchestrator()
    cache = {"CONTAORDEM": orchestrator.sheets._ws.get_all_values()}
    for sheet_name in ("T_EXTRATO", "DÍZIMOS/OFERTAS", "SAÍDAS", "Financeiro", "VC_VENDAS"):
        cache[sheet_name] = orchestrator._get_source_worksheet(sheet_name).get_all_values()
    cache_path = Path(tempfile.gettempdir()) / "soma_direct_reconciliation_cache.json"
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print("Cache das folhas carregado.", flush=True)
    failures = []
    for index, date in enumerate(dates, start=1):
        result = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "validate_perfect_date_batch.py"),
                "--date",
                date,
                "--apply",
                "--cache-file",
                str(cache_path),
                "--auto-delete-extras",
                *( ["--only-current-errors"] if args.recheck_errors else [] ),
            ],
            cwd=root,
            check=False,
        )
        if result.returncode != 0:
            time.sleep(60)
            result = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "validate_perfect_date_batch.py"),
                    "--date",
                    date,
                    "--apply",
                    "--cache-file",
                    str(cache_path),
                    "--auto-delete-extras",
                    *( ["--only-current-errors"] if args.recheck_errors else [] ),
                ],
                cwd=root,
                check=False,
            )
            if result.returncode != 0:
                failures.append(date)
        print(f"Progresso: {index}/{len(dates)} data={date} rc={result.returncode}", flush=True)
        time.sleep(3)
    print(f"Concluído: {len(dates) - len(failures)} | Falhas: {len(failures)} {failures}")


if __name__ == "__main__":
    main()
