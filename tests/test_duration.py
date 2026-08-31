"""Duration arithmetic — and a guard against the legacy hh.mm decimal habit."""

from datetime import timedelta

import pytest

from matrixreports.duration import (
    excel_time,
    hhmm,
    parse_hhmm,
    parse_legacy_decimal,
    summed,
    total_minutes,
)


def test_hhmm_does_not_wrap_past_a_day():
    assert hhmm(timedelta(hours=48, minutes=49)) == "48:49"
    assert hhmm(timedelta(hours=101, minutes=34)) == "101:34"


def test_hhmm_blank_for_missing_data():
    # "no punches" and "zero hours" are different facts and must look different.
    assert hhmm(None) == ""
    assert hhmm(timedelta(0)) == "00:00"


def test_negative_durations_keep_their_sign():
    assert hhmm(-timedelta(hours=1, minutes=30)) == "-01:30"


@pytest.mark.parametrize(
    "text,expected",
    [("54:00", timedelta(hours=54)), ("08:14", timedelta(hours=8, minutes=14)),
     ("8:14:30", timedelta(hours=8, minutes=14, seconds=30)), ("nonsense", None)],
)
def test_parse_hhmm(text, expected):
    assert parse_hhmm(text) == expected


def test_summing_durations_beats_the_legacy_decimal_sum():
    """The bug in the supplied weekly sheet, stated as a test.

    Week totals there are produced by SUM() over hh.mm decimals, so 48:49 plus
    52:45 is reported as 100.94 instead of 101:34 — 40 minutes adrift on two
    cells alone.
    """
    first, second = timedelta(hours=48, minutes=49), timedelta(hours=52, minutes=45)
    assert hhmm(summed([first, second])) == "101:34"
    legacy = 48.49 + 52.45
    assert f"{legacy:.2f}" == "100.94"
    assert total_minutes(summed([first, second])) != int(legacy * 60) // 100 * 100


def test_legacy_decimal_round_trip():
    assert parse_legacy_decimal(8.14) == timedelta(hours=8, minutes=14)
    assert parse_legacy_decimal(9.07) == timedelta(hours=9, minutes=7)


def test_excel_time_is_a_fraction_of_a_day():
    assert excel_time(timedelta(hours=12)) == pytest.approx(0.5)
    assert excel_time(timedelta(hours=36)) == pytest.approx(1.5)
    assert excel_time(None) is None
