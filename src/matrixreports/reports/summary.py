"""Daily exception summary.

The client currently rebuilds this by hand every morning, reading the daily
export and copying names into a dozen side-by-side blocks.  Every block here is
derived from the same day records the daily report uses, so the summary can no
longer disagree with the sheet it came from.

The blocks are emitted stacked rather than side by side: each is a real table
with its own header row, which means Excel can sort and filter them.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..builder import AttendanceBook
from ..config import Config
from ..model import Anomaly, DayRecord
from .base import Cell, Column, ReportTable, clock, duration, integer, text

BREAK_ALERT = timedelta(hours=1)


def _section(key: str, title: str, columns: list[Column], day: date, config: Config) -> ReportTable:
    return ReportTable(
        key=key,
        title=title,
        subtitle=f"{day:%d %B %Y (%A)}",
        company=config.company.name,
        columns=columns,
        meta={"day": day},
    )


def build_summary_report(
    book: AttendanceBook,
    day: date,
    *,
    break_alert: timedelta = BREAK_ALERT,
) -> list[ReportTable]:
    config = book.config
    shift = config.shift
    records = book.for_day(day)
    present = [record for record in records if record.has_data]
    sections: list[ReportTable] = []

    def name_of(record: DayRecord) -> str:
        return record.employee.display_name

    # ------------------------------------------------------------- early in
    early_in_cut = _combine(day, shift.early_in_before)
    early = [
        record for record in present
        if early_in_cut and record.first_in and record.first_in < early_in_cut
    ]
    table = _section(
        "summary_early_in",
        f"Early IN (before {shift.early_in_before:%H:%M})" if shift.early_in_before else "Early IN",
        [Column("#", "int", 5.0), Column("Employee Name", "text", 26.0),
         Column("Early IN Time", "time", 11.0), Column("Minutes Early", "duration", 11.0)],
        day, config,
    )
    for index, record in enumerate(sorted(early, key=lambda r: r.first_in), start=1):
        table.add_row([integer(index), text(name_of(record)), clock(record.first_in),
                       duration(early_in_cut - record.first_in)])
    sections.append(table)

    # -------------------------------------------------------------- late in
    late_cut = _combine(day, shift.late_in_threshold)
    late = [
        record for record in present
        if late_cut and record.first_in and record.first_in > late_cut
    ]
    table = _section(
        "summary_late_in",
        f"Late IN (after {shift.late_in_threshold:%H:%M})" if shift.late_in_threshold else "Late IN",
        [Column("#", "int", 5.0), Column("Employee Name", "text", 26.0),
         Column("1st IN", "time", 10.0), Column("Last Out", "time", 10.0),
         Column("Late By", "duration", 10.0), Column("Total Hours Worked", "duration", 13.0),
         Column("Comments", "text", 24.0)],
        day, config,
    )
    for index, record in enumerate(sorted(late, key=lambda r: r.first_in, reverse=True), start=1):
        table.add_row([
            integer(index), text(name_of(record)), clock(record.first_in),
            clock(record.last_out), duration(record.first_in - late_cut),
            duration(record.worked),
            text(f"Completed hours - {record.employee.department}"
                 if record.worked and record.worked >= shift.full_day and record.employee.department
                 else ""),
        ])
    sections.append(table)

    # ------------------------------------------------------------- late out
    late_out_cut = _shift_end_plus(day, config, shift.late_out_grace)
    late_outs = [
        record for record in present
        if late_out_cut and record.last_out and record.last_out > late_out_cut
    ]
    table = _section(
        "summary_late_out",
        f"Late OUT (after {late_out_cut:%H:%M})" if late_out_cut else "Late OUT",
        [Column("#", "int", 5.0), Column("Employee Name", "text", 26.0),
         Column("Last Out", "time", 10.0), Column("Stayed Past Shift", "duration", 13.0),
         Column("Total Hours", "duration", 11.0)],
        day, config,
    )
    for index, record in enumerate(
        sorted(late_outs, key=lambda r: r.last_out, reverse=True), start=1
    ):
        table.add_row([integer(index), text(name_of(record)), clock(record.last_out),
                       duration(record.last_out - late_out_cut), duration(record.span)])
    sections.append(table)

    # ------------------------------------------------------------ early out
    early_outs = [
        record for record in present
        if record.early_out and record.early_out > timedelta(0)
    ]
    table = _section(
        "summary_early_out", "Early OUT",
        [Column("#", "int", 5.0), Column("Employee Name", "text", 26.0),
         Column("Last Out", "time", 10.0), Column("Early By", "duration", 10.0),
         Column("Total Hours", "duration", 11.0), Column("Reason", "text", 24.0)],
        day, config,
    )
    for index, record in enumerate(
        sorted(early_outs, key=lambda r: r.early_out, reverse=True), start=1
    ):
        table.add_row([integer(index), text(name_of(record)), clock(record.last_out),
                       duration(record.early_out), duration(record.worked), text("")])
    sections.append(table)

    # ------------------------------------------------------ working on a day off
    off_workers = [record for record in present if not record.is_working_day]
    table = _section(
        "summary_working_off", "Working During Off / Holiday",
        [Column("#", "int", 5.0), Column("Employee Name", "text", 26.0),
         Column("1st In", "time", 10.0), Column("Last Out", "time", 10.0),
         Column("Total Hours", "duration", 11.0), Column("Reason", "text", 24.0)],
        day, config,
    )
    for index, record in enumerate(sorted(off_workers, key=name_of), start=1):
        table.add_row([integer(index), text(name_of(record)), clock(record.first_in),
                       clock(record.last_out), duration(record.worked), text("")])
    sections.append(table)

    # ----------------------------------------------------------- long breaks
    long_breaks = [
        record for record in present
        if record.break_total and record.break_total > break_alert
    ]
    hours = int(break_alert.total_seconds() // 3600)
    table = _section(
        "summary_long_break",
        f"Break Over {hours} Hour" if hours == 1 else f"Break Over {hours} Hours",
        [Column("#", "int", 5.0), Column("Employee Name", "text", 26.0),
         Column("Time Out", "duration", 11.0), Column("No. Of OUT", "int", 10.0),
         Column("Longest Break", "duration", 12.0), Column("Reason", "text", 24.0)],
        day, config,
    )
    for index, record in enumerate(
        sorted(long_breaks, key=lambda r: r.break_total, reverse=True), start=1
    ):
        longest = max((b.duration for b in record.breaks if b.duration), default=None)
        table.add_row([integer(index), text(name_of(record)), duration(record.break_total),
                       integer(record.break_count), duration(longest), text("")])
    sections.append(table)

    # ------------------------------------------------- missed punches / anomalies
    flagged = [record for record in present if _reportable(record)]
    table = _section(
        "summary_missed_punch", "Missed Time IN / OUT",
        [Column("#", "int", 5.0), Column("Employee Name", "text", 26.0),
         Column("1st In", "time", 10.0), Column("Last Out", "time", 10.0),
         Column("Punches", "int", 8.0), Column("Issue", "text", 30.0)],
        day, config,
    )
    for index, record in enumerate(sorted(flagged, key=name_of), start=1):
        table.add_row([
            integer(index), text(name_of(record)), clock(record.first_in),
            clock(record.last_out), integer(len(record.punches)),
            text(", ".join(a.value for a in record.anomalies if _reportable_flag(a))),
        ])
    sections.append(table)

    # ---------------------------------------------------------- not present
    absent = [record for record in records if not record.has_data]
    grouped: dict[str, list[DayRecord]] = {}
    for record in absent:
        grouped.setdefault(record.status or "Absent", []).append(record)
    table = _section(
        "summary_not_present", "Not Present",
        [Column("#", "int", 5.0), Column("Employee Name", "text", 26.0),
         Column("Category", "text", 18.0), Column("Department", "text", 18.0)],
        day, config,
    )
    index = 0
    for status in sorted(grouped):
        for record in sorted(grouped[status], key=name_of):
            index += 1
            table.add_row([integer(index), text(name_of(record)), text(status),
                           text(record.employee.department or "")])
    sections.append(table)

    # ------------------------------------------------------------- headcount
    table = _section(
        "summary_headcount", "Headcount",
        [Column("Category", "text", 24.0), Column("Count", "int", 8.0)],
        day, config,
    )
    table.add_row([text("On roll"), integer(len(records))])
    table.add_row([text("Present"), integer(len(present))])
    table.add_row([text("Not present"), integer(len(absent))])
    for status in sorted(grouped):
        table.add_row([text(f"  {status}"), integer(len(grouped[status]))])
    table.add_row([text("Late in"), integer(len(late))])
    table.add_row([text("Early out"), integer(len(early_outs))])
    table.add_row([text("Late out"), integer(len(late_outs))])
    table.add_row([text("Punch data issues"), integer(len(flagged))])
    sections.append(table)
    return sections


def _reportable_flag(flag: Anomaly) -> bool:
    return flag in {
        Anomaly.MISSING_IN,
        Anomaly.MISSING_OUT,
        Anomaly.CONSECUTIVE_IN,
        Anomaly.CONSECUTIVE_OUT,
        Anomaly.OPEN_SESSION_CLOSED,
    }


def _reportable(record: DayRecord) -> bool:
    return any(_reportable_flag(flag) for flag in record.anomalies)


def _combine(day: date, moment: time | None) -> datetime | None:
    return datetime.combine(day, moment) if moment else None


def _shift_end_plus(day: date, config: Config, grace: timedelta) -> datetime | None:
    shift = config.shift
    if not shift.end:
        return None
    end = datetime.combine(day, shift.end)
    if shift.start and shift.end <= shift.start:
        end += timedelta(days=1)
    return end + grace
