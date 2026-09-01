"""Weekly, monthly and yearly views.

All three are the same shape — employees down the side, periods across the top,
a duration in each cell — so they share one builder.  Totals are real durations,
which is the second half of the fix: the supplied weekly sheet totals ``48.49 +
52.45`` to ``100.94`` where the true sum of 48h49m and 52h45m is ``101:34``.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from ..builder import AttendanceBook
from ..config import Config
from ..duration import ZERO
from ..model import DayRecord
from .base import Cell, Column, ReportTable, code, duration, integer, percent, text


@dataclass(frozen=True, slots=True)
class Period:
    """One column of a grid report.

    ``band`` is the label in the merged row above the column headers: the
    weekday for a day column, the week number for a week. The client's sheets
    carry both, and they are how the sheet is actually read - a weekend is
    spotted by its day name, and a week is referred to by its number, not by
    its date range.
    """

    key: str
    label: str
    start: date
    end: date
    band: str = ""

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def daily_periods(book: AttendanceBook) -> list[Period]:
    return [
        Period(day.isoformat(), f"{day:%d}", day, day, band=f"{day:%a}")
        for day in book.days
    ]


def weekly_periods(book: AttendanceBook, week_start: int = 0) -> list[Period]:
    """Split the range into weeks beginning on ``week_start`` (0 = Monday)."""
    periods: list[Period] = []
    cursor = book.start - timedelta(days=(book.start.weekday() - week_start) % 7)
    while cursor <= book.end:
        end = cursor + timedelta(days=6)
        iso_year, iso_week, _ = cursor.isocalendar()
        periods.append(
            Period(
                f"{iso_year}-W{iso_week:02d}",
                f"{cursor:%d %b %Y} - {end:%d %b %Y}",
                cursor,
                end,
                band=f"Week {iso_week}",
            )
        )
        cursor = end + timedelta(days=1)
    return periods


def monthly_periods(book: AttendanceBook) -> list[Period]:
    periods: list[Period] = []
    cursor = book.start.replace(day=1)
    while cursor <= book.end:
        last_day = calendar.monthrange(cursor.year, cursor.month)[1]
        end = cursor.replace(day=last_day)
        periods.append(Period(f"{cursor:%Y-%m}", f"{cursor:%b %Y}", cursor, end,
                              band=f"{cursor:%Y}"))
        cursor = end + timedelta(days=1)
    return periods


def _cell_for_day(record: DayRecord | None, config: Config) -> Cell:
    """A day cell: hours worked, or the status code when nobody worked."""
    if record is None:
        return text("")
    if record.has_data:
        return duration(record.worked)
    return code(record.status or config.report.absent_label)


def _status_counts(records: list[DayRecord], config: Config) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        if record.has_data:
            continue
        counts[record.status] = counts.get(record.status, 0) + 1
    return counts


def build_grid_report(
    book: AttendanceBook,
    periods: list[Period],
    *,
    key: str,
    title: str,
    show_day_codes: bool,
) -> ReportTable:
    """Employees down, ``periods`` across, worked duration in each cell."""
    config = book.config
    report = config.report

    status_labels = _distinct_statuses(book, config)

    columns: list[Column] = [
        Column("#", "int", 5.0),
        Column("Employee Name", "text", 26.0),
        Column("Department", "text", 16.0),
    ]
    for period in periods:
        columns.append(Column(period.label, "duration", 10.0, group=period.key))
    columns += [
        Column("Total Hours", "duration", 12.0),
        Column("Days Present", "int", 10.0),
        Column("Days Absent", "int", 10.0),
    ]
    columns += [Column(label, "int", 11.0) for label in status_labels]
    columns += [
        Column("Avg Hours / Present Day", "duration", 14.0),
        Column("Attendance %", "percent", 11.0),
    ]

    leading, trailing = 3, len(columns) - 3 - len(periods)
    group_spans: list[tuple[str, int]] = []
    if any(period.band for period in periods):
        group_spans.append(("", leading))
        # Merge runs of the same band. Months in one year would otherwise
        # repeat "2026" twelve times, which is noise; merged, it reads as one
        # year heading, and a range crossing years still splits where it should.
        for period in periods:
            if group_spans and group_spans[-1][0] == period.band and period.band:
                label, span = group_spans[-1]
                group_spans[-1] = (label, span + 1)
            else:
                group_spans.append((period.band, 1))
        if trailing:
            group_spans.append(("", trailing))
    if len(group_spans) == 2:
        group_spans = []          # a single band over everything says nothing

    table = ReportTable(
        key=key,
        title=title,
        subtitle=f"{book.start:%d %b %Y} to {book.end:%d %b %Y}",
        company=config.company.name,
        columns=columns,
        group_spans=group_spans,
        meta={"periods": [p.key for p in periods]},
    )

    for index, employee in enumerate(
        sorted(book.employees, key=lambda e: e.display_name.lower()), start=1
    ):
        records = book.for_employee(employee.emp_id)
        by_day = {record.day: record for record in records}
        cells: list[Cell] = [
            integer(index),
            text(employee.display_name),
            text(employee.department or ""),
        ]

        total = ZERO
        for period in periods:
            in_period = [
                record for day, record in by_day.items() if period.contains(day)
            ]
            if show_day_codes and period.start == period.end:
                cells.append(_cell_for_day(by_day.get(period.start), config))
                worked = by_day.get(period.start)
                if worked and worked.worked:
                    total += worked.worked
            else:
                worked_records = [r for r in in_period if r.worked]
                if not in_period:
                    cells.append(duration(None))
                elif not worked_records:
                    cells.append(code(_dominant_status(in_period, config)))
                else:
                    period_total = sum((r.worked for r in worked_records), ZERO)
                    cells.append(duration(period_total))
                    total += period_total

        present_days = sum(1 for record in records if record.has_data)
        working_days = sum(1 for record in records if record.is_working_day)
        absent_days = sum(
            1 for record in records
            if not record.has_data and record.status == report.absent_label
        )
        counts = _status_counts(records, config)

        cells.append(duration(total))
        cells.append(integer(present_days))
        cells.append(integer(absent_days))
        for label in status_labels:
            cells.append(integer(counts.get(label, 0)))
        cells.append(duration(total / present_days if present_days else None))
        cells.append(percent(present_days / working_days if working_days else None))
        table.add_row(cells)

    return table


def _distinct_statuses(book: AttendanceBook, config: Config) -> list[str]:
    """Every non-working status seen in the period, as its own count column."""
    seen: set[str] = set()
    for record in book.records.values():
        if not record.has_data and record.status:
            seen.add(record.status)
    seen.discard(config.report.absent_label)
    return sorted(seen)


def _dominant_status(records: list[DayRecord], config: Config) -> str:
    counts = _status_counts(records, config)
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def build_weekly_report(book: AttendanceBook, *, week_start: int = 0) -> ReportTable:
    return build_grid_report(
        book,
        weekly_periods(book, week_start),
        key="weekly",
        title="Attendance Weekly Summary Report",
        show_day_codes=False,
    )


def build_monthly_report(book: AttendanceBook) -> ReportTable:
    return build_grid_report(
        book,
        daily_periods(book),
        key="monthly",
        title=f"Monthly Attendance — {book.start:%B %Y}",
        show_day_codes=True,
    )


def build_yearly_report(book: AttendanceBook) -> ReportTable:
    return build_grid_report(
        book,
        monthly_periods(book),
        key="yearly",
        title=f"Yearly Attendance Report — {book.start:%Y}",
        show_day_codes=False,
    )
