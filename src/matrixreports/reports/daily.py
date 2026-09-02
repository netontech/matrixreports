"""Daily attendance report — the one the six-in/out limit actually broke.

Layout follows the sheet the client already circulates (``1st In``, ``Last Out``,
repeating ``OUT / IN / MINS`` groups, then the totals block), so the people
reading it do not have to relearn anything.  The single difference is that the
repeating block is sized from the data instead of being fixed at five breaks.
"""

from __future__ import annotations

from datetime import date

from ..builder import AttendanceBook
from ..config import Config
from ..model import DayRecord
from .base import (
    Cell,
    Column,
    ReportTable,
    clock,
    duration,
    group_columns,
    integer,
    text,
)


def group_count(book: AttendanceBook, config: Config) -> int:
    """How many OUT/IN groups this report needs.

    Sized to the busiest day in the period, floored at ``min_inout_groups`` so a
    quiet week still looks like the familiar sheet, and capped only if the
    client explicitly sets ``report.max_inout_groups``.
    """
    needed = book.max_breaks
    report = config.report
    if report.min_inout_groups:
        needed = max(needed, report.min_inout_groups)
    if report.max_inout_groups:
        needed = min(needed, report.max_inout_groups)
    return needed


def build_daily_report(
    book: AttendanceBook,
    day: date,
    *,
    groups: int | None = None,
) -> ReportTable:
    config = book.config
    groups = group_count(book, config) if groups is None else groups
    records = book.for_day(day)

    columns: list[Column] = [
        Column("#", "int", 5.0),
        Column("Employee Name", "text", 26.0),
        Column("Department", "text", 16.0),
        Column("1st In", "time", 10.0),
        Column("Last Out", "time", 10.0),
    ]
    columns += group_columns(groups)
    columns += [
        Column("Final OUT", "time", 10.0),
        Column("No. Of OUT", "int", 9.0),
        Column("Total Out Time", "duration", 11.0),
        Column("Actual Works Hours", "duration", 12.0),
        Column("Wrk Hrs + Out Time", "duration", 12.0),
        Column("Late IN", "duration", 9.0),
        Column("Early OUT", "duration", 9.5),
        Column("Late OUT", "duration", 9.5),
        Column("Remarks", "text", 16.0),
    ]
    if config.report.include_anomaly_column:
        columns.append(Column("Punches", "int", 8.0))
        columns.append(Column("Data Issues", "text", 26.0))

    table = ReportTable(
        key="daily",
        title=f"Employee Daily Attendance — {day:%d %B %Y (%A)}",
        subtitle=_shift_label(config),
        company=config.company.name,
        columns=columns,
        meta={"day": day, "groups": groups},
    )
    table.group_spans = _group_spans(groups, trailing=len(columns) - 5 - groups * 3)

    # The client's sheet runs one alphabetical list and counts present and
    # non-present staff separately, so "#" reads as "the Nth person in" and
    # "the Nth person out". Listing all the present first and then restarting
    # turns that convention into what looks like a numbering fault, so keep
    # everyone in one alphabetical run, exactly as their sheet does.
    present_serial = absent_serial = 0
    for record in sorted(records, key=lambda r: r.employee.display_name.lower()):
        if record.has_data:
            present_serial += 1
            serial = present_serial
        else:
            absent_serial += 1
            serial = absent_serial
        table.add_row(_row(record, serial, groups, config))

    truncated = [r for r in records if r.break_count > groups]
    if truncated:
        names = ", ".join(sorted(r.employee.display_name for r in truncated))
        table.notes.append(
            f"{len(truncated)} employee(s) have more breaks than the "
            f"{groups} displayed groups: {names}. Raise report.max_inout_groups "
            "or leave it at 0 to size automatically."
        )
    return table


def _shift_label(config: Config) -> str:
    shift = config.shift
    if not shift.start or not shift.end:
        return ""
    return f"Shift {shift.start:%H:%M} to {shift.end:%H:%M}"


def _group_spans(groups: int, *, trailing: int) -> list[tuple[str, int]]:
    """The banded row above the header that numbers each OUT/IN group."""
    spans: list[tuple[str, int]] = [("", 5)]
    for index in range(1, groups + 1):
        spans.append((str(index), 3))
    spans.append(("", trailing))
    return spans


def _row(record: DayRecord, serial: int, groups: int, config: Config) -> list[Cell]:
    employee = record.employee
    cells: list[Cell] = [
        integer(serial),
        text(employee.display_name),
        text(employee.department or ""),
        clock(record.first_in),
        clock(record.last_out),
    ]

    for index in range(groups):
        if index < len(record.breaks):
            gap = record.breaks[index]
            cells.append(clock(gap.start))
            cells.append(clock(gap.end))
            cells.append(duration(gap.duration))
        else:
            cells.extend([clock(None), clock(None), duration(None)])

    cells += [
        clock(record.last_out),
        integer(record.break_count),
        duration(record.break_total),
        duration(record.worked),
        duration(record.span),
        duration(record.late_in),
        duration(record.early_out),
        duration(record.late_out),
        text(record.status or config.report.present_label),
    ]
    if config.report.include_anomaly_column:
        cells.append(integer(len(record.punches)))
        cells.append(text(", ".join(a.value for a in record.anomalies if a.value != "NO_PUNCHES")))
    return cells
