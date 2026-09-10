from datetime import datetime

import pytest

from ronda_completa import parse_date, resolve_interval, row_date_in_interval


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
