"""Domain model: punches, intervals and a day's attendance record.

The central design point of this package is that :class:`DayRecord` holds
*lists* of sessions and breaks whose length is bounded only by the data.  The
Matrix report writer flattens a day into a fixed six ``OUT``/``IN`` groups and
silently drops everything past the fifth break; here nothing is dropped, and the
Excel exporter sizes its columns to whatever the data actually contains.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum

from .duration import ZERO, summed


class Direction(str, Enum):
    IN = "IN"
    OUT = "OUT"
    UNKNOWN = "UNKNOWN"


class Anomaly(str, Enum):
    """Data-quality flags raised while pairing punches."""

    MISSING_IN = "MISSING_IN"
    MISSING_OUT = "MISSING_OUT"
    DUPLICATE_PUNCH = "DUPLICATE_PUNCH"
    CONSECUTIVE_IN = "CONSECUTIVE_IN"
    CONSECUTIVE_OUT = "CONSECUTIVE_OUT"
    DIRECTION_INFERRED = "DIRECTION_INFERRED"
    OPEN_SESSION_CLOSED = "OPEN_SESSION_CLOSED"
    CROSSES_MIDNIGHT = "CROSSES_MIDNIGHT"
    NO_PUNCHES = "NO_PUNCHES"


@dataclass(frozen=True, slots=True)
class Employee:
    emp_id: str
    name: str
    code: str | None = None
    department: str | None = None
    designation: str | None = None
    shift: str | None = None
    active: bool = True

    @property
    def display_name(self) -> str:
        return (self.name or self.code or self.emp_id or "").strip()


@dataclass(frozen=True, slots=True)
class Punch:
    """A single raw read from the door controller."""

    emp_id: str
    timestamp: datetime
    direction: Direction = Direction.UNKNOWN
    device: str | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class Interval:
    """A closed time interval. ``end is None`` means still open."""

    start: datetime
    end: datetime | None = None

    @property
    def duration(self) -> timedelta | None:
        if self.end is None:
            return None
        return self.end - self.start

    @property
    def is_open(self) -> bool:
        return self.end is None


@dataclass(slots=True)
class DayRecord:
    """One employee's attendance for one calendar day.

    ``sessions`` are the periods actually inside the building; ``breaks`` are the
    gaps between them.  For an employee who punched 23 times there are 12
    sessions and 11 breaks, and all of them survive to the report.
    """

    employee: Employee
    day: date
    punches: list[Punch] = field(default_factory=list)
    sessions: list[Interval] = field(default_factory=list)
    breaks: list[Interval] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    status: str = ""          # Present / Sick Leave / OFF / HOL ...
    is_working_day: bool = True
    shift_start: datetime | None = None
    shift_end: datetime | None = None
    remarks: str = ""

    # ------------------------------------------------------------------ times
    @property
    def first_in(self) -> datetime | None:
        return self.sessions[0].start if self.sessions else None

    @property
    def last_out(self) -> datetime | None:
        for session in reversed(self.sessions):
            if session.end is not None:
                return session.end
        return None

    @property
    def has_data(self) -> bool:
        return bool(self.punches)

    # ---------------------------------------------------------------- metrics
    @property
    def worked(self) -> timedelta | None:
        """Time actually inside the building: the sum of all sessions."""
        if not self.sessions:
            return None
        return summed(session.duration for session in self.sessions)

    @property
    def break_total(self) -> timedelta | None:
        """Total time out during the day: the sum of all breaks."""
        if not self.sessions:
            return None
        return summed(brk.duration for brk in self.breaks)

    @property
    def span(self) -> timedelta | None:
        """First in to last out, i.e. worked time plus breaks."""
        if self.first_in is None or self.last_out is None:
            return None
        return self.last_out - self.first_in

    @property
    def break_count(self) -> int:
        return len(self.breaks)

    @property
    def late_in(self) -> timedelta | None:
        """How late the first punch was against the shift start (plus grace)."""
        if self.first_in is None or self.shift_start is None:
            return None
        delta = self.first_in - self.shift_start
        return delta if delta > ZERO else ZERO

    @property
    def early_out(self) -> timedelta | None:
        """How early the last punch was against the shift end."""
        if self.last_out is None or self.shift_end is None:
            return None
        delta = self.shift_end - self.last_out
        return delta if delta > ZERO else ZERO

    @property
    def late_out(self) -> timedelta | None:
        """How long past the shift end the employee stayed."""
        if self.last_out is None or self.shift_end is None:
            return None
        delta = self.last_out - self.shift_end
        return delta if delta > ZERO else ZERO

    @property
    def has_open_session(self) -> bool:
        return any(session.is_open for session in self.sessions)
