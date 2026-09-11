from datetime import date

import pytest

from hypothesis_exporter.cli import _sync_dates, build_parser


def test_since_is_inclusive_through_today():
    days = _sync_dates("today", "2026-09-09", "Europe/Rome", today=date(2026, 9, 11))
    assert days == [date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]


def test_single_date_defaults_to_today():
    assert _sync_dates(None, None, "Europe/Rome", today=date(2026, 9, 11)) == [date(2026, 9, 11)]


def test_since_rejects_future_date():
    with pytest.raises(RuntimeError, match="later than today"):
        _sync_dates(None, "2026-09-12", "Europe/Rome", today=date(2026, 9, 11))


def test_date_and_since_are_mutually_exclusive():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["sync", "--date", "today", "--since", "2026-09-01"])
