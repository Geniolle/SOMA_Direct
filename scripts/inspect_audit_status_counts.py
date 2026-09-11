from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from config.settings import Settings
from services.sheets_service import GoogleSheetsService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    rows = GoogleSheetsService(Settings.from_env()).get_all_rows(only_entrada_saida=True)
    errors = defaultdict(Counter)
    for row in rows:
        if row.auditoria.strip().startswith("Erro"):
            errors[row.data_mov.strip()][row.auditoria.strip()] += 1
    if errors:
        if args.summary:
            statuses = [status for date_statuses in errors.values() for status in date_statuses]
            print(json.dumps({
                "datas_com_erro": len(errors),
                "datas_erro_soma": sum(any(status.startswith("Erro SOMA:") for status in values) for values in errors.values()),
                "datas_erro_quantidade": sum(any(status.startswith("Erro: Quantidade") for status in values) for values in errors.values()),
                "linhas_com_erro": sum(sum(values.values()) for values in errors.values()),
                "extras_soma": sum(int(match.group(1)) for status in statuses if (match := re.search(r"extras=(\d+)", status))),
                "ausentes_soma": sum(int(match.group(1)) for status in statuses if (match := re.search(r"ausentes=(\d+)", status))),
            }, ensure_ascii=False, indent=2))
            return
        print(json.dumps({date: dict(statuses) for date, statuses in errors.items()}, ensure_ascii=False, indent=2))
        return
    counts = Counter(row.auditoria.strip() for row in rows)
    print(json.dumps(dict(counts), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
