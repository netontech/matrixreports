"""Report builders: column layout scales with the data, totals are exact."""

from datetime import date, timedelta

import pytest

from matrixreports.builder import AttendanceBuilder
from matrixreports.duration import hhmm
from matrixreports.reports import (
    build_daily_report,
    build_monthly_report,
    build_summary_report,
    build_weekly_report,
    group_count,
)

from conftest import DAY


@pytest.fixture
def book(config, source):
    return AttendanceBuilder(config, source).build(DAY, DAY)


def test_book_reports_the_deepest_day(book):
    assert book.max_punches == 24
    assert book.max_breaks == 11


def test_daily_report_grows_past_the_six_group_limit(book, config):
    table = build_daily_report(book, DAY)
    assert table.meta["groups"] == 11
    # 5 leading columns + 11 groups x 3 + 9 trailing + 2 diagnostic columns.
    assert len(table.columns) == 5 + 11 * 3 + 9 + 2
    headers = [column.header for column in table.columns]
    assert headers.count("MINS") == 11
    for row in table.rows:
        assert len(row) == len(table.columns)


def test_every_break_reaches_the_daily_report(book):
    table = build_daily_report(book, DAY)
    names = [row[1].value for row in table.rows]
    heavy = table.rows[names.index("Heavy Walker")]
    breaks = book.record("1", DAY).breaks
    # Break i occupies the three cells starting at column 5 + 3i.
    for index, gap in enumerate(breaks):
        offset = 5 + index * 3
        assert heavy[offset].value == gap.start
        assert heavy[offset + 1].value == gap.end
        assert heavy[offset + 2].value == gap.duration
    no_of_out = headers_index(table, "No. Of OUT")
    assert heavy[no_of_out].value == 11


def headers_index(table, header: str) -> int:
    return [column.header for column in table.columns].index(header)


def test_daily_totals_are_internally_consistent(book):
    table = build_daily_report(book, DAY)
    worked = headers_index(table, "Actual Works Hours")
    out_time = headers_index(table, "Total Out Time")
    span = headers_index(table, "Wrk Hrs + Out Time")
    for row in table.rows:
        if row[worked].value is None:
            continue
        assert row[worked].value + row[out_time].value == row[span].value


def test_quiet_day_keeps_the_familiar_minimum_width(config, employees):
    from matrixreports.datasource import InMemoryPunchSource
    from conftest import LIGHT_DAY, punches

    source = InMemoryPunchSource(employees[:2], {"2": punches("2", LIGHT_DAY)})
    book = AttendanceBuilder(config, source).build(DAY, DAY)
    assert book.max_breaks == 2
    assert group_count(book, config) == 5      # floored at min_inout_groups


def test_group_count_can_be_pinned(config, source):
    config.report.max_inout_groups = 6
    book = AttendanceBuilder(config, source).build(DAY, DAY)
    assert group_count(book, config) == 6
    table = build_daily_report(book, DAY)
    # Pinning is allowed, but the report says plainly that data is being hidden.
    assert table.notes and "more breaks than" in table.notes[0]


def test_leave_status_flows_into_the_report(book):
    table = build_daily_report(book, DAY)
    remarks = headers_index(table, "Remarks")
    statuses = {row[1].value: row[remarks].value for row in table.rows}
    assert statuses["On Leave"] == "Sick Leave"
    assert statuses["Heavy Walker"] == "Present"


def test_summary_sections_are_built(book):
    sections = build_summary_report(book, DAY)
    keys = {section.key for section in sections}
    assert "summary_late_in" in keys
    assert "summary_not_present" in keys
    assert "summary_headcount" in keys
    headcount = next(s for s in sections if s.key == "summary_headcount")
    values = {row[0].value: row[1].value for row in headcount.rows}
    assert values["On roll"] == 3
    assert values["Present"] == 2
    assert values["Not present"] == 1


def test_summary_flags_long_breaks_worst_first(book):
    """Heavy Walker is out 01:12 across 11 breaks; Light Walker 00:50 across 2."""
    sections = build_summary_report(book, DAY, break_alert=timedelta(hours=1))
    long_breaks = next(s for s in sections if s.key == "summary_long_break")
    assert [row[1].value for row in long_breaks.rows] == ["Heavy Walker"]

    sections = build_summary_report(book, DAY, break_alert=timedelta(minutes=30))
    long_breaks = next(s for s in sections if s.key == "summary_long_break")
    assert [row[1].value for row in long_breaks.rows] == ["Heavy Walker", "Light Walker"]
    assert hhmm(long_breaks.rows[0][2].value) == "01:12"
    assert long_breaks.rows[0][3].value == 11


def test_weekly_totals_use_exact_duration_arithmetic(config, employees):
    from matrixreports.datasource import InMemoryPunchSource
    from conftest import punches

    # Two days of 8:49 and 8:45 -> 17:34, not the 17.94 a decimal sum would give.
    data = {
        "2": punches("2", ["10:00", "18:49"], date(2026, 6, 1))
        + punches("2", ["10:00", "18:45"], date(2026, 6, 2))
    }
    source = InMemoryPunchSource(employees[:2], data)
    book = AttendanceBuilder(config, source).build(date(2026, 6, 1), date(2026, 6, 2))
    table = build_weekly_report(book)
    total = [column.header for column in table.columns].index("Total Hours")
    row = next(row for row in table.rows if row[1].value == "Light Walker")
    assert hhmm(row[total].value) == "17:34"


def test_monthly_grid_has_one_column_per_day(config, source):
    book = AttendanceBuilder(config, source).build(date(2026, 6, 1), date(2026, 6, 30))
    table = build_monthly_report(book)
    day_columns = [c for c in table.columns if c.style == "duration" and c.group]
    assert len(day_columns) == 30


def test_monthly_grid_shows_status_codes_for_non_working_days(config, source):
    book = AttendanceBuilder(config, source).build(date(2026, 6, 1), date(2026, 6, 7))
    table = build_monthly_report(book)
    row = next(row for row in table.rows if row[1].value == "On Leave")
    codes = {cell.value for cell in row if cell.style == "code"}
    assert "Sick Leave" in codes      # 1 June, from the leave register
    assert "OFF" in codes             # 5 June is the configured weekly off


def test_group_band_spans_cover_every_column(book):
    """The numbered band above the header must line up with the OUT columns."""
    table = build_daily_report(book, DAY)
    assert sum(span for _, span in table.group_spans) == len(table.columns)
    position = 1
    for label, span in table.group_spans:
        if label:
            # A numbered band starts exactly on that group's OUT column.
            assert table.columns[position - 1].header == "OUT"
            assert table.columns[position - 1].group == f"Break {label}"
        position += span
