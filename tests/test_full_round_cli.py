from collections import Counter
from datetime import datetime

import pytest

from ronda_completa import (
    parse_date,
    print_final_result,
    resolve_interval,
    row_date_in_interval,
)


def test_resolve_interval_uses_prompt(monkeypatch):
    answers = iter(["01/08/2026", "31/08/2026"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    start, end = resolve_interval()

    assert start == datetime(2026, 8, 1)
    assert end == datetime(2026, 8, 31)


def test_resolve_interval_rejects_inverted_dates():
    with pytest.raises(ValueError, match="data inicial"):
        resolve_interval("31/08/2026", "01/08/2026")


def test_date_parser_and_interval_filter():
    start = parse_date("01/08/2026")
    end = parse_date("31/08/2026")

    assert row_date_in_interval("15/08/2026", start, end)
    assert not row_date_in_interval("01/09/2026", start, end)


def test_date_parser_accepts_date_without_separators():
    assert parse_date("09092026") == datetime(2026, 9, 9)


def test_final_result_is_clear_when_there_are_no_divergences(capsys):
    stats = Counter(confirmados=6, inversa_confirmada=6)

    print_final_result(
        datetime(2026, 9, 9),
        datetime(2026, 9, 9),
        True,
        stats,
        sheet_rows_count=6,
        soma_items_count=6,
        reverse_errors=[],
    )

    output = capsys.readouterr().out
    assert "Status: CONCLUÍDO SEM DIVERGÊNCIAS" in output
    assert "Linhas analisadas: 6" in output
    assert "Linhas corrigidas com segurança: 0" in output
    assert "Documentos vinculados corretamente: 6" in output
    assert "Nenhuma divergência encontrada nas duas validações." in output
