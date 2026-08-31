"""Configuration loading.

Everything installation-specific lives in YAML: the database connection, the
*names* of the Matrix tables and columns, the shift rules and the leave codes.
The Matrix schema differs between COSEC versions and deployments, so the
mapping is data rather than code — pointing this tool at the client's database
is an edit to one YAML file, not a change to any Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, timedelta
from pathlib import Path
from typing import Any

import yaml

from .duration import parse_hhmm
from .pairing import PairingPolicy


def _parse_time(value: Any) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) < 2:
        raise ValueError(f"expected HH:MM, got {value!r}")
    return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)


def _parse_duration(value: Any, default: timedelta | None = None) -> timedelta | None:
    if value in (None, ""):
        return default
    if isinstance(value, timedelta):
        return value
    parsed = parse_hhmm(str(value))
    if parsed is None:
        raise ValueError(f"expected HH:MM duration, got {value!r}")
    return parsed


@dataclass(slots=True)
class CompanyConfig:
    name: str = ""
    address: str = ""
    report_footer: str = ""


@dataclass(slots=True)
class DatabaseConfig:
    """Connection settings. ``driver`` selects the DB-API module at runtime."""

    driver: str = "sqlite"          # sqlite | sqlserver | mysql | postgres
    host: str = ""
    port: int | None = None
    database: str = ""
    user: str = ""
    password: str = ""
    odbc_driver: str = "ODBC Driver 17 for SQL Server"
    dsn: str = ""                   # full connection string, wins over the parts
    path: str = ""                  # sqlite file
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TableMapping:
    """Which table and columns hold a given entity."""

    table: str = ""
    columns: dict[str, str] = field(default_factory=dict)
    where: str = ""                 # extra static predicate, e.g. "STATUS = 'A'"

    def column(self, logical: str, default: str | None = None) -> str:
        name = self.columns.get(logical, default)
        if not name:
            raise KeyError(
                f"column {logical!r} is not mapped for table {self.table!r}; "
                "add it under schema.<entity>.columns in the config"
            )
        return name


@dataclass(slots=True)
class SchemaConfig:
    """The Matrix database layout, expressed as names this tool can substitute."""

    employees: TableMapping = field(default_factory=TableMapping)
    punches: TableMapping = field(default_factory=TableMapping)
    leave: TableMapping = field(default_factory=TableMapping)
    holidays: TableMapping = field(default_factory=TableMapping)
    # How the punch table encodes direction. Values are compared as strings,
    # case-insensitively; anything unlisted is treated as UNKNOWN and the
    # direction is then inferred by alternation.
    direction_in: list[str] = field(default_factory=lambda: ["1", "I", "IN", "ENTRY", "TRUE"])
    direction_out: list[str] = field(default_factory=lambda: ["2", "O", "OUT", "EXIT", "FALSE"])
    # Some installations split the stamp across a DATE and a TIME column.
    punch_datetime_is_split: bool = False


@dataclass(slots=True)
class ShiftConfig:
    start: time | None = time(10, 0)
    end: time | None = time(19, 0)
    late_in_grace: timedelta = timedelta(minutes=10)
    late_out_grace: timedelta = timedelta(minutes=10)
    full_day: timedelta = timedelta(hours=9)
    half_day: timedelta = timedelta(hours=4, minutes=30)
    early_in_before: time | None = time(10, 0)
    weekly_off_days: list[int] = field(default_factory=list)   # 0=Mon .. 6=Sun

    @property
    def late_in_threshold(self) -> time | None:
        """The clock time after which an arrival counts as late."""
        if self.start is None:
            return None
        base = timedelta(hours=self.start.hour, minutes=self.start.minute) + self.late_in_grace
        total = int(base.total_seconds() // 60)
        return time((total // 60) % 24, total % 60)


@dataclass(slots=True)
class ReportConfig:
    """Presentation choices for the exported workbooks."""

    # The old writer hard-coded six groups. 0 means "size to the data".
    max_inout_groups: int = 0
    min_inout_groups: int = 5           # keep the familiar layout for quiet days
    include_anomaly_column: bool = True
    include_seconds: bool = False
    absent_label: str = "A"
    off_label: str = "OFF"
    holiday_label: str = "HOL"
    present_label: str = "Present"


@dataclass(slots=True)
class Config:
    company: CompanyConfig = field(default_factory=CompanyConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    schema: SchemaConfig = field(default_factory=SchemaConfig)
    shift: ShiftConfig = field(default_factory=ShiftConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    pairing: PairingPolicy = field(default_factory=PairingPolicy)
    queries: dict[str, str] = field(default_factory=dict)   # full SQL overrides
    leave_codes: dict[str, str] = field(default_factory=dict)
    source_path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        config = cls.from_dict(raw)
        config.source_path = path
        return config

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        company = CompanyConfig(**(raw.get("company") or {}))

        database = DatabaseConfig(**(raw.get("database") or {}))

        schema_raw = dict(raw.get("schema") or {})
        schema = SchemaConfig(
            employees=TableMapping(**(schema_raw.get("employees") or {})),
            punches=TableMapping(**(schema_raw.get("punches") or {})),
            leave=TableMapping(**(schema_raw.get("leave") or {})),
            holidays=TableMapping(**(schema_raw.get("holidays") or {})),
            direction_in=[str(v) for v in (schema_raw.get("direction_in") or
                                           ["1", "I", "IN", "ENTRY", "TRUE"])],
            direction_out=[str(v) for v in (schema_raw.get("direction_out") or
                                            ["2", "O", "OUT", "EXIT", "FALSE"])],
            punch_datetime_is_split=bool(schema_raw.get("punch_datetime_is_split", False)),
        )

        shift_raw = dict(raw.get("shift") or {})
        shift = ShiftConfig(
            start=_parse_time(shift_raw.get("start", "10:00")),
            end=_parse_time(shift_raw.get("end", "19:00")),
            late_in_grace=_parse_duration(shift_raw.get("late_in_grace"), timedelta(minutes=10)),
            late_out_grace=_parse_duration(shift_raw.get("late_out_grace"), timedelta(minutes=10)),
            full_day=_parse_duration(shift_raw.get("full_day"), timedelta(hours=9)),
            half_day=_parse_duration(shift_raw.get("half_day"), timedelta(hours=4, minutes=30)),
            early_in_before=_parse_time(shift_raw.get("early_in_before", "10:00")),
            weekly_off_days=list(shift_raw.get("weekly_off_days") or []),
        )

        report = ReportConfig(**(raw.get("report") or {}))
        pairing = PairingPolicy(**(raw.get("pairing") or {}))

        return cls(
            company=company,
            database=database,
            schema=schema,
            shift=shift,
            report=report,
            pairing=pairing,
            queries=dict(raw.get("queries") or {}),
            leave_codes=dict(raw.get("leave_codes") or {}),
        )
