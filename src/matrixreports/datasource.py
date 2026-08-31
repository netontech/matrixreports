"""Reading punches out of the Matrix database (or an offline extract).

Two things matter here.  First, the SQL is *generated from the schema mapping*,
so the same code works whatever the client's tables happen to be called.
Second, no query aggregates or truncates: we pull the raw punch rows for the
period and do all pairing in Python.  The six-in/out ceiling in the stock
reports comes from a pivot that allocates six fixed slots per day; by keeping
the rows long and narrow until the very last step, there is no slot to run out
of.
"""

from __future__ import annotations

import csv
import sqlite3
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import Config, DatabaseConfig, SchemaConfig
from .model import Direction, Employee, Punch

# Placeholder style per driver: qmark for sqlite/odbc, format for mysql.
_PARAMSTYLE = {
    "sqlite": "?",
    "sqlserver": "?",
    "mysql": "%s",
    "postgres": "%s",
}


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp format: {value!r}")


def _coerce_time_of_day(value: Any) -> timedelta:
    """Parse the TIME half of a split date/time punch column."""
    if value is None or value == "":
        return timedelta(0)
    if isinstance(value, timedelta):
        return value
    if hasattr(value, "hour") and not isinstance(value, str):
        return timedelta(hours=value.hour, minutes=value.minute,
                         seconds=getattr(value, "second", 0))
    parts = str(value).strip().split(":")
    while len(parts) < 3:
        parts.append("0")
    return timedelta(hours=int(parts[0]), minutes=int(parts[1]), seconds=int(float(parts[2])))


class PunchSource(ABC):
    """Anything that can hand back employees and their raw punches."""

    @abstractmethod
    def employees(self) -> list[Employee]:
        ...

    @abstractmethod
    def punches(self, start: date, end: date) -> dict[str, list[Punch]]:
        """Raw punches per employee id, for ``start`` to ``end`` inclusive."""

    def leave(self, start: date, end: date) -> dict[tuple[str, date], str]:
        """Leave/absence codes keyed by (employee id, day). Optional."""
        return {}

    def holidays(self, start: date, end: date) -> dict[date, str]:
        """Public holidays in the period. Optional."""
        return {}

    def close(self) -> None:
        return None

    def __enter__(self) -> "PunchSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class SqlPunchSource(PunchSource):
    """Generic DB-API source driven by the schema mapping in the config."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.schema: SchemaConfig = config.schema
        self.driver = (config.database.driver or "sqlite").lower()
        self.placeholder = _PARAMSTYLE.get(self.driver, "?")
        self.connection = self._connect(config.database)

    # ------------------------------------------------------------- connection
    def _connect(self, settings: DatabaseConfig):
        driver = self.driver
        if driver == "sqlite":
            return sqlite3.connect(settings.path or settings.database or ":memory:")
        if driver == "sqlserver":
            if settings.dsn:
                connection_string = settings.dsn
            else:
                port = f",{settings.port}" if settings.port else ""
                connection_string = (
                    f"DRIVER={{{settings.odbc_driver}}};"
                    f"SERVER={settings.host}{port};DATABASE={settings.database};"
                    f"UID={settings.user};PWD={settings.password}"
                )
            try:
                import pyodbc
            except ImportError as exc:      # pragma: no cover - env dependent
                raise RuntimeError(
                    "SQL Server access needs pyodbc: pip install 'matrixreports[sqlserver]'"
                ) from exc
            return pyodbc.connect(connection_string)
        if driver == "mysql":
            try:
                import pymysql
            except ImportError as exc:      # pragma: no cover - env dependent
                raise RuntimeError(
                    "MySQL access needs PyMySQL: pip install 'matrixreports[mysql]'"
                ) from exc
            return pymysql.connect(
                host=settings.host or "localhost",
                port=settings.port or 3306,
                user=settings.user,
                password=settings.password,
                database=settings.database,
                **settings.extra,
            )
        if driver == "postgres":
            try:
                import psycopg2
            except ImportError as exc:      # pragma: no cover - env dependent
                raise RuntimeError(
                    "PostgreSQL access needs psycopg2: pip install 'matrixreports[postgres]'"
                ) from exc
            return psycopg2.connect(
                host=settings.host or "localhost",
                port=settings.port or 5432,
                user=settings.user,
                password=settings.password,
                dbname=settings.database,
                **settings.extra,
            )
        raise ValueError(f"unsupported database driver: {settings.driver!r}")

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception:                   # pragma: no cover - best effort
            pass

    def _run(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, tuple(params))
            return cursor.fetchall()
        finally:
            cursor.close()

    # ---------------------------------------------------------------- queries
    def employees(self) -> list[Employee]:
        override = self.config.queries.get("employees")
        mapping = self.schema.employees
        if override:
            rows = self._run(override)
        else:
            columns = [
                mapping.column("id"),
                mapping.column("name"),
                mapping.columns.get("code"),
                mapping.columns.get("department"),
                mapping.columns.get("designation"),
                mapping.columns.get("shift"),
            ]
            selected = ", ".join(c if c else "NULL" for c in columns)
            where = f" WHERE {mapping.where}" if mapping.where else ""
            rows = self._run(f"SELECT {selected} FROM {mapping.table}{where}")
        result: list[Employee] = []
        for row in rows:
            values = list(row) + [None] * (6 - len(row))
            result.append(
                Employee(
                    emp_id=str(values[0]),
                    name=(values[1] or "").strip() if values[1] else str(values[0]),
                    code=str(values[2]) if values[2] is not None else None,
                    department=values[3],
                    designation=values[4],
                    shift=values[5],
                )
            )
        return result

    def punches(self, start: date, end: date) -> dict[str, list[Punch]]:
        override = self.config.queries.get("punches")
        mapping = self.schema.punches
        # The window is widened by a day at each end so that a night shift's
        # punches on either side of midnight are all available to the pairer.
        low = datetime.combine(start - timedelta(days=1), datetime.min.time())
        high = datetime.combine(end + timedelta(days=1), datetime.max.time())
        placeholder = self.placeholder

        if override:
            rows = self._run(override, (low, high))
        elif self.schema.punch_datetime_is_split:
            emp_col = mapping.column("emp_id")
            date_col = mapping.column("date")
            time_col = mapping.column("time")
            direction_col = mapping.columns.get("direction")
            device_col = mapping.columns.get("device")
            selected = ", ".join([
                emp_col, date_col, time_col,
                direction_col or "NULL", device_col or "NULL",
            ])
            where = f" AND {mapping.where}" if mapping.where else ""
            rows = self._run(
                f"SELECT {selected} FROM {mapping.table} "
                f"WHERE {date_col} BETWEEN {placeholder} AND {placeholder}{where} "
                f"ORDER BY {emp_col}, {date_col}, {time_col}",
                (low.date(), high.date()),
            )
            rows = [
                (r[0], _coerce_datetime(r[1]) + _coerce_time_of_day(r[2]), r[3], r[4])
                for r in rows
            ]
        else:
            emp_col = mapping.column("emp_id")
            stamp_col = mapping.column("timestamp")
            direction_col = mapping.columns.get("direction")
            device_col = mapping.columns.get("device")
            selected = ", ".join([
                emp_col, stamp_col, direction_col or "NULL", device_col or "NULL",
            ])
            where = f" AND {mapping.where}" if mapping.where else ""
            rows = self._run(
                f"SELECT {selected} FROM {mapping.table} "
                f"WHERE {stamp_col} BETWEEN {placeholder} AND {placeholder}{where} "
                f"ORDER BY {emp_col}, {stamp_col}",
                (low, high),
            )

        return self._rows_to_punches(rows)

    def _rows_to_punches(self, rows: Iterable[Sequence[Any]]) -> dict[str, list[Punch]]:
        by_employee: dict[str, list[Punch]] = defaultdict(list)
        ins = {value.strip().upper() for value in self.schema.direction_in}
        outs = {value.strip().upper() for value in self.schema.direction_out}
        for row in rows:
            values = list(row) + [None] * (4 - len(row))
            emp_id = str(values[0])
            stamp = _coerce_datetime(values[1])
            if stamp is None:
                continue
            raw_direction = values[2]
            if raw_direction is None:
                direction = Direction.UNKNOWN
            else:
                token = str(raw_direction).strip().upper()
                if token in ins:
                    direction = Direction.IN
                elif token in outs:
                    direction = Direction.OUT
                else:
                    direction = Direction.UNKNOWN
            by_employee[emp_id].append(
                Punch(emp_id, stamp, direction, str(values[3]) if values[3] else None, "db")
            )
        for punches in by_employee.values():
            punches.sort(key=lambda p: p.timestamp)
        return dict(by_employee)

    def leave(self, start: date, end: date) -> dict[tuple[str, date], str]:
        override = self.config.queries.get("leave")
        mapping = self.schema.leave
        if not override and not mapping.table:
            return {}
        placeholder = self.placeholder
        if override:
            rows = self._run(override, (start, end))
        else:
            emp_col = mapping.column("emp_id")
            from_col = mapping.column("date_from")
            to_col = mapping.columns.get("date_to") or from_col
            code_col = mapping.column("code")
            where = f" AND {mapping.where}" if mapping.where else ""
            rows = self._run(
                f"SELECT {emp_col}, {from_col}, {to_col}, {code_col} FROM {mapping.table} "
                f"WHERE {to_col} >= {placeholder} AND {from_col} <= {placeholder}{where}",
                (start, end),
            )
        result: dict[tuple[str, date], str] = {}
        for emp_id, date_from, date_to, code in rows:
            first = _coerce_datetime(date_from).date()
            last = _coerce_datetime(date_to).date() if date_to else first
            day = max(first, start)
            while day <= min(last, end):
                result[(str(emp_id), day)] = str(code)
                day += timedelta(days=1)
        return result

    def holidays(self, start: date, end: date) -> dict[date, str]:
        override = self.config.queries.get("holidays")
        mapping = self.schema.holidays
        if not override and not mapping.table:
            return {}
        placeholder = self.placeholder
        if override:
            rows = self._run(override, (start, end))
        else:
            date_col = mapping.column("date")
            name_col = mapping.columns.get("name") or "NULL"
            where = f" AND {mapping.where}" if mapping.where else ""
            rows = self._run(
                f"SELECT {date_col}, {name_col} FROM {mapping.table} "
                f"WHERE {date_col} BETWEEN {placeholder} AND {placeholder}{where}",
                (start, end),
            )
        return {_coerce_datetime(row[0]).date(): (row[1] or "Holiday") for row in rows}


class CsvPunchSource(PunchSource):
    """Offline source: a CSV/TSV extract of the punch table.

    This exists so the client can run the reports today by exporting the raw
    punch rows from SSMS, without waiting for network access to the Matrix
    server to be arranged.  Expected columns (case-insensitive, extras ignored):
    ``emp_id, name, timestamp[, direction, device, department]``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        delimiter: str = ",",
        schema: SchemaConfig | None = None,
    ) -> None:
        self.path = Path(path)
        self.delimiter = delimiter
        self.schema = schema or SchemaConfig()
        self._employees: dict[str, Employee] = {}
        self._punches: dict[str, list[Punch]] = defaultdict(list)
        self._load()

    def _load(self) -> None:
        ins = {value.strip().upper() for value in self.schema.direction_in}
        outs = {value.strip().upper() for value in self.schema.direction_out}
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=self.delimiter)
            normalised = {name.lower().strip(): name for name in (reader.fieldnames or [])}

            def pick(row: dict[str, str], *names: str) -> Any:
                for name in names:
                    key = normalised.get(name)
                    if key is not None and row.get(key) not in (None, ""):
                        return row[key]
                return None

            for row in reader:
                emp_id = pick(row, "emp_id", "employee_id", "empcode", "code", "id")
                if emp_id is None:
                    continue
                emp_id = str(emp_id).strip()
                stamp_raw = pick(row, "timestamp", "punch_time", "datetime", "date_time")
                if stamp_raw is None:
                    day = pick(row, "date", "punch_date")
                    clock = pick(row, "time", "punch_time")
                    if day is None:
                        continue
                    stamp = _coerce_datetime(day) + _coerce_time_of_day(clock)
                else:
                    stamp = _coerce_datetime(stamp_raw)
                token = pick(row, "direction", "inout", "in_out", "type")
                if token is None:
                    direction = Direction.UNKNOWN
                else:
                    token = str(token).strip().upper()
                    direction = (
                        Direction.IN if token in ins
                        else Direction.OUT if token in outs
                        else Direction.UNKNOWN
                    )
                if emp_id not in self._employees:
                    self._employees[emp_id] = Employee(
                        emp_id=emp_id,
                        name=str(pick(row, "name", "employee_name") or emp_id).strip(),
                        code=str(pick(row, "code", "empcode") or emp_id).strip(),
                        department=pick(row, "department", "dept"),
                    )
                self._punches[emp_id].append(
                    Punch(emp_id, stamp, direction, pick(row, "device", "door"), "csv")
                )
        for punches in self._punches.values():
            punches.sort(key=lambda p: p.timestamp)

    def employees(self) -> list[Employee]:
        return sorted(self._employees.values(), key=lambda e: e.display_name.lower())

    def punches(self, start: date, end: date) -> dict[str, list[Punch]]:
        low = datetime.combine(start - timedelta(days=1), datetime.min.time())
        high = datetime.combine(end + timedelta(days=1), datetime.max.time())
        return {
            emp_id: [p for p in punches if low <= p.timestamp <= high]
            for emp_id, punches in self._punches.items()
        }


class InMemoryPunchSource(PunchSource):
    """Trivial source used by the tests and by callers that already hold data."""

    def __init__(
        self,
        employees: Sequence[Employee],
        punches: dict[str, list[Punch]],
        leave: dict[tuple[str, date], str] | None = None,
        holidays: dict[date, str] | None = None,
    ) -> None:
        self._employees = list(employees)
        self._punches = {k: sorted(v, key=lambda p: p.timestamp) for k, v in punches.items()}
        self._leave = leave or {}
        self._holidays = holidays or {}

    def employees(self) -> list[Employee]:
        return list(self._employees)

    def punches(self, start: date, end: date) -> dict[str, list[Punch]]:
        low = datetime.combine(start - timedelta(days=1), datetime.min.time())
        high = datetime.combine(end + timedelta(days=1), datetime.max.time())
        return {
            emp_id: [p for p in punches if low <= p.timestamp <= high]
            for emp_id, punches in self._punches.items()
        }

    def leave(self, start: date, end: date) -> dict[tuple[str, date], str]:
        return {k: v for k, v in self._leave.items() if start <= k[1] <= end}

    def holidays(self, start: date, end: date) -> dict[date, str]:
        return {k: v for k, v in self._holidays.items() if start <= k <= end}


def open_source(config: Config, *, csv_path: str | Path | None = None) -> PunchSource:
    """Pick a source: an explicit CSV extract, otherwise the configured database."""
    if csv_path:
        return CsvPunchSource(csv_path, schema=config.schema)
    return SqlPunchSource(config)
