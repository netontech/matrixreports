"""Assemble day records for a whole period, for every employee.

Everything downstream — daily, weekly, monthly, yearly — is a view over the same
:class:`AttendanceBook`.  The legacy process rebuilt each report by hand from a
different export, which is how the same employee ends up with different totals in
the monthly and the weekly workbook for the same week.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import Config
from .datasource import PunchSource
from .model import DayRecord, Employee, Punch
from .pairing import attendance_day, build_day_record


def date_range(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(span + 1)]


@dataclass(slots=True)
class AttendanceBook:
    """All day records for a period, indexed for quick slicing."""

    config: Config
    start: date
    end: date
    employees: list[Employee]
    days: list[date]
    records: dict[tuple[str, date], DayRecord] = field(default_factory=dict)
    holidays: dict[date, str] = field(default_factory=dict)

    def record(self, emp_id: str, day: date) -> DayRecord | None:
        return self.records.get((emp_id, day))

    def for_day(self, day: date) -> list[DayRecord]:
        return [
            self.records[(employee.emp_id, day)]
            for employee in self.employees
            if (employee.emp_id, day) in self.records
        ]

    def for_employee(self, emp_id: str) -> list[DayRecord]:
        return [self.records[(emp_id, day)] for day in self.days if (emp_id, day) in self.records]

    @property
    def max_breaks(self) -> int:
        """The widest OUT/IN block the period needs.

        This is the number the exporter uses to size its columns — the value the
        stock Matrix report pins at five no matter what the data holds.
        """
        return max((record.break_count for record in self.records.values()), default=0)

    @property
    def max_punches(self) -> int:
        return max((len(record.punches) for record in self.records.values()), default=0)


class AttendanceBuilder:
    """Reads a source and folds raw punches into an :class:`AttendanceBook`."""

    def __init__(self, config: Config, source: PunchSource) -> None:
        self.config = config
        self.source = source

    def build(
        self,
        start: date,
        end: date,
        *,
        employee_ids: list[str] | None = None,
    ) -> AttendanceBook:
        config = self.config
        employees = self.source.employees()
        if employee_ids:
            wanted = {str(value) for value in employee_ids}
            employees = [
                employee for employee in employees
                if employee.emp_id in wanted or (employee.code or "") in wanted
            ]

        punches = self.source.punches(start, end)
        leave = self.source.leave(start, end)
        holidays = self.source.holidays(start, end)
        days = date_range(start, end)

        book = AttendanceBook(
            config=config,
            start=start,
            end=end,
            employees=employees,
            days=days,
            holidays=holidays,
        )

        for employee in employees:
            by_day = self._group_by_day(punches.get(employee.emp_id, []))
            for day in days:
                day_punches = by_day.get(day, [])
                leave_code = leave.get((employee.emp_id, day))
                record = build_day_record(
                    employee,
                    day,
                    day_punches,
                    policy=config.pairing,
                    shift_start_time=config.shift.start,
                    shift_end_time=config.shift.end,
                )
                record.is_working_day = self._is_working_day(day, holidays)
                record.status = self._status(record, leave_code, holidays)
                record.remarks = record.status
                book.records[(employee.emp_id, day)] = record
        return book

    def _group_by_day(self, punches: list[Punch]) -> dict[date, list[Punch]]:
        grouped: dict[date, list[Punch]] = {}
        for punch in punches:
            day = attendance_day(punch.timestamp, self.config.pairing)
            grouped.setdefault(day, []).append(punch)
        return grouped

    def _is_working_day(self, day: date, holidays: dict[date, str]) -> bool:
        if day in holidays:
            return False
        return day.weekday() not in set(self.config.shift.weekly_off_days)

    def _status(
        self,
        record: DayRecord,
        leave_code: str | None,
        holidays: dict[date, str],
    ) -> str:
        report = self.config.report
        codes = self.config.leave_codes
        if leave_code:
            return codes.get(str(leave_code), str(leave_code))
        if record.has_data:
            # Attendance on a non-working day is real work and is reported as
            # such; the summary sheet surfaces it under "working during off".
            return report.present_label
        if record.day in holidays:
            return report.holiday_label
        if not record.is_working_day:
            return report.off_label
        return report.absent_label
