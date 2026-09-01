"""Work out the Matrix schema by inspecting the database.

The COSEC layout differs between versions and installations, so rather than
asking someone to hand-map twenty table and column names, this module reads the
catalogue, scores every table for how well it fits each role, and writes a draft
config.  The operator then runs ``check`` to confirm it before trusting a report.

It also looks for the table that causes the problem in the first place: a
pre-flattened daily summary with ``IN1, OUT1, IN2, OUT2 ...`` columns -- or, as
Matrix actually names them, ``Punch1 ... Punch12`` -- can only hold as many
punches as it has slot columns, which is where a six-in/out ceiling comes from.  Those tables are reported as a warning, never selected as a source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

DATETIME_TYPES = {"datetime", "datetime2", "smalldatetime", "datetimeoffset",
                  "timestamp", "timestamp without time zone", "timestamp with time zone"}
DATE_TYPES = {"date"}
TIME_TYPES = {"time", "time without time zone"}
NUMERIC_TYPES = {"int", "integer", "bigint", "smallint", "tinyint", "numeric",
                 "decimal", "float", "real", "bit", "double", "money"}

# Columns like IN1/OUT1/IN2 -- or Matrix's own Punch1..Punch12 -- mark a
# pre-flattened summary: its width is the cap.  Real COSEC installations number
# the slots rather than naming them by direction, so both spellings must match
# or the warning silently never fires on the very schema it exists for.
FLATTENED_RE = re.compile(r"^(IN|OUT|I|O|PUNCH)_?(\d{1,2})$", re.IGNORECASE)

EMPLOYEE_ID_HINTS = ["EMPLOYEEID", "EMPID", "EMP_ID", "EMPCODE", "EMP_CODE",
                     "USERID", "USER_ID", "PERSONID", "STAFFID", "CARDHOLDERID",
                     "MEMBERID", "EMPLOYEECODE", "EMPLOYEE"]
NAME_HINTS = ["EMPLOYEENAME", "EMPNAME", "FULLNAME", "PERSONNAME", "USERNAME",
              "STAFFNAME", "NAME", "FIRSTNAME"]
DEPARTMENT_HINTS = ["DEPARTMENT", "DEPTNAME", "DEPT", "DIVISION", "SECTION"]
DESIGNATION_HINTS = ["DESIGNATION", "GRADE", "TITLE", "POSITION"]
SHIFT_HINTS = ["SHIFTCODE", "SHIFT", "SHIFTID"]
DIRECTION_HINTS = ["INOUT", "IN_OUT", "INOUTFLAG", "IOFLAG", "DIRECTION",
                   "EVENTTYPE", "PUNCHTYPE", "ATTENDANCETYPE", "MODE", "IOMODE",
                   "ENTRYEXIT", "TYPE", "FLAG", "STATUS"]
DEVICE_HINTS = ["DOORNAME", "DOOR", "DEVICENAME", "DEVICE", "READER", "TERMINAL",
                "PANEL", "MACHINE", "LOCATION"]
LEAVE_CODE_HINTS = ["LEAVETYPE", "LEAVECODE", "TYPE", "CODE", "LEAVE"]
DATE_FROM_HINTS = ["LEAVEFROM", "FROMDATE", "DATEFROM", "STARTDATE", "FROM_DATE"]
DATE_TO_HINTS = ["LEAVETO", "TODATE", "DATETO", "ENDDATE", "TO_DATE"]
HOLIDAY_DATE_HINTS = ["HOLIDAYDATE", "HDATE", "DATE"]
HOLIDAY_NAME_HINTS = ["HOLIDAYNAME", "DESCRIPTION", "NAME", "REASON"]


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    name: str
    data_type: str

    @property
    def normalised(self) -> str:
        return re.sub(r"[^A-Z0-9]", "", self.name.upper())

    @property
    def is_datetime(self) -> bool:
        return self.data_type.lower() in DATETIME_TYPES

    @property
    def is_date(self) -> bool:
        return self.data_type.lower() in DATE_TYPES

    @property
    def is_time(self) -> bool:
        return self.data_type.lower() in TIME_TYPES

    @property
    def is_numeric(self) -> bool:
        return self.data_type.lower() in NUMERIC_TYPES


@dataclass(slots=True)
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: int | None = None

    def find(self, hints: list[str], *, predicate=None) -> str | None:
        column, _ = self.find_typed(hints, predicate=predicate)
        return column

    def find_typed(self, hints: list[str], *, predicate=None) -> tuple[str | None, bool]:
        """Find a column by name hint, preferring one of the expected type.

        Returns ``(name, typed)``. A column whose name matches but whose type
        does not is still returned, with ``typed=False`` — installations that
        store timestamps and dates in varchar columns are common, and the value
        parsers handle them, but the caller should say so in its reasons.
        """
        def search(pool: list[ColumnInfo]) -> str | None:
            for hint in hints:
                needle = hint.replace("_", "")
                for column in pool:
                    if column.normalised == needle:
                        return column.name
            for hint in hints:
                needle = hint.replace("_", "")
                for column in pool:
                    if needle in column.normalised:
                        return column.name
            return None

        if predicate is not None:
            typed_pool = [column for column in self.columns if predicate(column)]
            found = search(typed_pool)
            if found:
                return found, True
            # Fall back to any column whose name fits, excluding obviously
            # numeric ones which are never a stamp or a name.
            loose = [column for column in self.columns if not column.is_numeric]
            return search(loose), False
        return search(self.columns), True

    @property
    def flattened_slots(self) -> int:
        """How many IN1/OUT1-style slot columns this table has."""
        return sum(1 for column in self.columns if FLATTENED_RE.match(column.name))

    @property
    def datetime_columns(self) -> list[ColumnInfo]:
        return [column for column in self.columns if column.is_datetime]


@dataclass(slots=True)
class Candidate:
    table: TableInfo
    score: int
    reasons: list[str] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.table.name


@dataclass(slots=True)
class Discovery:
    tables: list[TableInfo] = field(default_factory=list)
    punches: list[Candidate] = field(default_factory=list)
    employees: list[Candidate] = field(default_factory=list)
    leave: list[Candidate] = field(default_factory=list)
    holidays: list[Candidate] = field(default_factory=list)
    flattened: list[TableInfo] = field(default_factory=list)
    direction_values: list[str] = field(default_factory=list)
    split_datetime: bool = False

    def best(self, role: str) -> Candidate | None:
        candidates = getattr(self, role)
        return candidates[0] if candidates else None


# --------------------------------------------------------------------- reading
def introspect(connection, driver: str) -> list[TableInfo]:
    """Read table and column names from the database catalogue."""
    driver = driver.lower()
    cursor = connection.cursor()
    try:
        if driver == "sqlite":
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
            names = [row[0] for row in cursor.fetchall()]
            tables = []
            for name in names:
                cursor.execute(f'PRAGMA table_info("{name}")')
                columns = [ColumnInfo(row[1], (row[2] or "").split("(")[0].lower())
                           for row in cursor.fetchall()]
                cursor.execute(f'SELECT COUNT(*) FROM "{name}"')
                tables.append(TableInfo(name, columns, cursor.fetchone()[0]))
            return tables

        cursor.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        grouped: dict[str, list[ColumnInfo]] = {}
        for table_name, column_name, data_type in cursor.fetchall():
            grouped.setdefault(str(table_name), []).append(
                ColumnInfo(str(column_name), str(data_type).lower())
            )
        tables = [TableInfo(name, columns) for name, columns in grouped.items()]
        _attach_row_counts(cursor, tables, driver)
        return tables
    finally:
        cursor.close()


def _attach_row_counts(cursor, tables: list[TableInfo], driver: str) -> None:
    """Row counts come from catalogue statistics — never a COUNT(*) sweep."""
    counts: dict[str, int] = {}
    try:
        if driver == "sqlserver":
            cursor.execute(
                "SELECT t.name, SUM(p.rows) FROM sys.tables t "
                "JOIN sys.partitions p ON p.object_id = t.object_id "
                "AND p.index_id IN (0, 1) GROUP BY t.name"
            )
        elif driver == "mysql":
            cursor.execute(
                "SELECT TABLE_NAME, TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE()"
            )
        elif driver == "postgres":
            cursor.execute(
                "SELECT relname, reltuples::bigint FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind = 'r' AND n.nspname NOT IN "
                "('pg_catalog', 'information_schema')"
            )
        else:
            return
        counts = {str(row[0]): int(row[1] or 0) for row in cursor.fetchall()}
    except Exception:              # statistics are a convenience, not a requirement
        return
    for table in tables:
        table.row_count = counts.get(table.name)


# --------------------------------------------------------------------- scoring
def _name_score(name: str, words: list[str]) -> tuple[int, list[str]]:
    upper = name.upper()
    hits = [word for word in words if word in upper]
    return len(hits) * 20, [f"name contains {word!r}" for word in hits]


def score_punch_table(table: TableInfo, employee_columns: set[str]) -> Candidate | None:
    """A punch table has one row per badge read: a stamp plus an employee."""
    score, reasons = _name_score(
        table.name, ["ATT", "PUNCH", "LOG", "EVENT", "SWIPE", "TRANS", "RAW", "MUSTER"]
    )
    roles: dict[str, str] = {}

    emp_column = table.find(EMPLOYEE_ID_HINTS)
    if not emp_column:
        return None
    roles["emp_id"] = emp_column
    score += 25
    reasons.append(f"employee column {emp_column!r}")
    if emp_column.upper() in employee_columns:
        score += 20
        reasons.append("employee column matches the employee master")

    stamp_hints = ["PUNCHDATETIME", "EVENTTIME", "LOGDATETIME", "ATTDATETIME",
                   "DATETIME", "PUNCHTIME", "EVENTDATETIME", "TRANSDATETIME",
                   "DATE_TIME"]
    stamp, typed = table.find_typed(
        stamp_hints, predicate=lambda column: column.is_datetime
    )
    if not stamp and table.datetime_columns:
        stamp, typed = table.datetime_columns[0].name, True

    date_column, date_typed = table.find_typed(
        ["PUNCHDATE", "ATTDATE", "LOGDATE", "DATE"],
        predicate=lambda column: column.is_date,
    )
    time_column, time_typed = table.find_typed(
        ["PUNCHTIME", "LOGTIME", "TIME"],
        predicate=lambda column: column.is_time,
    )
    # A genuine split stamp needs two separate columns of the right types.
    split_available = bool(
        date_column and time_column and date_column != time_column
        and (date_typed or time_typed)
    )

    if stamp and not (split_available and not typed):
        roles["timestamp"] = stamp
        score += 40 if typed else 25
        reasons.append(
            f"stamp column {stamp!r}" if typed
            else f"stamp column {stamp!r} (stored as text — verify the format)"
        )
    elif split_available:
        roles["date"] = date_column
        roles["time"] = time_column
        score += 35
        reasons.append(f"split stamp {date_column!r} + {time_column!r}")
    else:
        return None

    direction = table.find(DIRECTION_HINTS)
    if direction:
        roles["direction"] = direction
        score += 15
        reasons.append(f"direction column {direction!r}")

    device = table.find(DEVICE_HINTS)
    if device:
        roles["device"] = device
        reasons.append(f"device column {device!r}")

    slots = table.flattened_slots
    if slots >= 2:
        # Exactly the shape that caps a report. Push it below every raw log.
        score -= 200
        reasons.append(
            f"REJECTED: {slots} fixed punch slot columns — this is a "
            "pre-flattened summary, not the raw log"
        )

    if table.row_count:
        if table.row_count > 100_000:
            score += 20
            reasons.append(f"{table.row_count:,} rows")
        elif table.row_count > 1_000:
            score += 10
            reasons.append(f"{table.row_count:,} rows")
    return Candidate(table, score, reasons, roles)


def score_employee_table(table: TableInfo) -> Candidate | None:
    score, reasons = _name_score(
        table.name, ["EMPLOYEE", "EMP", "STAFF", "PERSON", "USER", "MEMBER", "MASTER"]
    )
    roles: dict[str, str] = {}

    identifier = table.find(EMPLOYEE_ID_HINTS)
    name_column = table.find(NAME_HINTS, predicate=lambda column: not column.is_numeric)
    if not identifier or not name_column:
        return None
    roles["id"] = identifier
    roles["name"] = name_column
    score += 45
    reasons.append(f"id {identifier!r}, name {name_column!r}")

    for role, hints in (
        ("code", ["EMPLOYEECODE", "EMPCODE", "CODE", "CARDNO", "BADGE"]),
        ("department", DEPARTMENT_HINTS),
        ("designation", DESIGNATION_HINTS),
        ("shift", SHIFT_HINTS),
    ):
        found = table.find(hints)
        if found and found != identifier and found != name_column:
            roles[role] = found
            score += 5
            reasons.append(f"{role} {found!r}")

    if table.flattened_slots >= 2:
        score -= 200
        reasons.append("REJECTED: fixed punch slot columns")
    if table.row_count is not None and 0 < table.row_count < 20_000:
        score += 10
        reasons.append(f"{table.row_count:,} rows")
    return Candidate(table, score, reasons, roles)


def score_leave_table(table: TableInfo) -> Candidate | None:
    score, reasons = _name_score(table.name, ["LEAVE", "ABSEN", "VACATION"])
    if score == 0:
        return None
    roles: dict[str, str] = {}
    identifier = table.find(EMPLOYEE_ID_HINTS)
    date_from, _ = table.find_typed(
        DATE_FROM_HINTS, predicate=lambda c: c.is_date or c.is_datetime
    )
    if not identifier or not date_from:
        return None
    roles["emp_id"] = identifier
    roles["date_from"] = date_from
    date_to, _ = table.find_typed(
        DATE_TO_HINTS, predicate=lambda c: c.is_date or c.is_datetime
    )
    if date_to:
        roles["date_to"] = date_to
    code = table.find(LEAVE_CODE_HINTS)
    if code:
        roles["code"] = code
    score += 40
    reasons.append(f"employee {identifier!r}, from {date_from!r}")
    return Candidate(table, score, reasons, roles)


def score_holiday_table(table: TableInfo) -> Candidate | None:
    score, reasons = _name_score(table.name, ["HOLIDAY", "HOLI"])
    if score == 0:
        return None
    date_column, _ = table.find_typed(
        HOLIDAY_DATE_HINTS, predicate=lambda c: c.is_date or c.is_datetime
    )
    if not date_column:
        return None
    roles = {"date": date_column}
    name_column = table.find(HOLIDAY_NAME_HINTS,
                             predicate=lambda c: not c.is_numeric)
    if name_column and name_column != date_column:
        roles["name"] = name_column
    reasons.append(f"date {date_column!r}")
    return Candidate(table, score + 30, reasons, roles)


def discover(connection, driver: str, *, sample_directions: bool = True) -> Discovery:
    """Inspect the database and rank candidate tables for each role."""
    tables = introspect(connection, driver)
    result = Discovery(tables=tables)

    result.employees = sorted(
        (c for c in (score_employee_table(t) for t in tables) if c),
        key=lambda c: -c.score,
    )
    employee_columns = set()
    if result.employees:
        employee_columns = {
            column.name.upper() for column in result.employees[0].table.columns
        }

    result.punches = sorted(
        (c for c in (score_punch_table(t, employee_columns) for t in tables) if c),
        key=lambda c: -c.score,
    )
    result.leave = sorted(
        (c for c in (score_leave_table(t) for t in tables) if c), key=lambda c: -c.score
    )
    result.holidays = sorted(
        (c for c in (score_holiday_table(t) for t in tables) if c),
        key=lambda c: -c.score,
    )
    result.flattened = [table for table in tables if table.flattened_slots >= 2]

    best = result.best("punches")
    if best:
        result.split_datetime = "timestamp" not in best.roles
        if sample_directions and "direction" in best.roles:
            result.direction_values = _sample_column(
                connection, best.name, best.roles["direction"], driver
            )
    return result


def _sample_column(connection, table: str, column: str, driver: str) -> list[str]:
    """The distinct values of the direction column, to map IN and OUT."""
    limit = ("SELECT DISTINCT TOP 20" if driver == "sqlserver"
             else "SELECT DISTINCT")
    suffix = "" if driver == "sqlserver" else " LIMIT 20"
    cursor = connection.cursor()
    try:
        cursor.execute(f"{limit} {column} FROM {table}{suffix}")
        return sorted(
            str(row[0]) for row in cursor.fetchall() if row[0] is not None
        )
    except Exception:
        return []
    finally:
        cursor.close()


# --------------------------------------------------------------------- output
def render_config(result: Discovery, *, database: dict | None = None) -> str:
    """Emit a draft config YAML, annotated with what was found and why."""
    lines: list[str] = [
        "# Generated by `matrixreports discover` on "
        f"{datetime.now():%Y-%m-%d %H:%M}.",
        "#",
        "# This is a DRAFT. Every mapping below was inferred from the database",
        "# catalogue. Run `matrixreports check --from <date> --to <date>` to",
        "# confirm it before trusting any report.",
        "",
    ]

    database = database or {}
    lines.append("database:")
    for key in ("driver", "host", "port", "database", "user", "odbc_driver", "dsn", "path"):
        if database.get(key) not in (None, "", 0):
            lines.append(f"  {key}: {_yaml_scalar(database[key])}")
    if database.get("driver") != "sqlite":
        lines.append('  password: ""      # fill in, or use a trusted connection via dsn')
    lines.append("")

    if result.flattened:
        lines.append("# WARNING - pre-flattened summary tables found. Each can hold")
        lines.append("# only as many punches as it has slot columns, which is")
        lines.append("# where a fixed in/out limit comes from. Not used as a source:")
        for table in result.flattened:
            lines.append(
                f"#   {table.name} ({table.flattened_slots} slot columns)"
            )
        lines.append("")

    lines.append("schema:")
    lines.extend(_render_role(result, "employees", "employees"))
    lines.extend(_render_role(result, "punches", "punches"))

    if result.split_datetime:
        lines.append("  # This punch table splits the stamp across two columns.")
        lines.append("  punch_datetime_is_split: true")
    else:
        lines.append("  punch_datetime_is_split: false")

    if result.direction_values:
        lines.append("")
        lines.append("  # Distinct values found in the direction column: "
                     + ", ".join(repr(value) for value in result.direction_values))
        lines.append("  # CHECK THIS SPLIT — getting it backwards inverts every "
                     "session.")
        ins, outs = _guess_direction_split(result.direction_values)
        lines.append(f"  direction_in:  {_yaml_list(ins)}")
        lines.append(f"  direction_out: {_yaml_list(outs)}")
    else:
        lines.append("")
        lines.append("  # No direction column found or no values sampled. Punches")
        lines.append("  # will be paired by alternation (see pairing.direction_mode).")

    lines.extend(_render_role(result, "leave", "leave"))
    lines.extend(_render_role(result, "holidays", "holidays"))

    lines.extend([
        "",
        "shift:",
        '  start: "10:00"',
        '  end: "19:00"',
        '  late_in_grace: "00:10"',
        '  late_out_grace: "00:10"',
        '  full_day: "09:00"',
        "  weekly_off_days: [4]        # 0=Mon .. 6=Sun",
        "",
        "pairing:",
        "  dedup_seconds: 60",
        f"  direction_mode: {'device' if result.direction_values else 'alternate'}",
        "  open_session_policy: shift_end",
        "  day_start_hour: 0           # set to 4 if shifts run past midnight",
        "",
        "report:",
        "  max_inout_groups: 0         # 0 = size the OUT/IN block to the data",
        "  min_inout_groups: 5",
        "",
        "leave_codes: {}               # map the codes in the leave table to labels",
    ])
    return "\n".join(lines) + "\n"


def _render_role(result: Discovery, role: str, key: str) -> list[str]:
    candidates = getattr(result, role)
    if not candidates:
        return [
            "",
            f"  # No {key} table identified. Fill this in by hand if one exists.",
            f"  # {key}:",
            "  #   table: ",
            "  #   columns: {}",
        ]
    best = candidates[0]
    lines = ["", f"  # {best.name}: {'; '.join(best.reasons)}"]
    if len(candidates) > 1:
        alternatives = ", ".join(
            f"{c.name} ({c.score})" for c in candidates[1:4]
        )
        lines.append(f"  # other candidates: {alternatives}")
    lines.append(f"  {key}:")
    lines.append(f"    table: {best.name}")
    lines.append("    columns:")
    for logical, column in best.roles.items():
        lines.append(f"      {logical}: {column}")
    lines.append('    where: ""')
    return lines


def _guess_direction_split(values: list[str]) -> tuple[list[str], list[str]]:
    """Split sampled direction values into IN-like and OUT-like sets."""
    ins: list[str] = []
    outs: list[str] = []
    for value in values:
        token = value.strip().upper()
        if token in {"1", "I", "IN", "ENTRY", "TRUE", "E"}:
            ins.append(value)
        elif token in {"0", "2", "O", "OUT", "EXIT", "FALSE", "X"}:
            outs.append(value)
    if not ins and not outs and len(values) == 2:
        # Two unknown values: assume the lower sorts as the entry, and say so.
        ins, outs = [values[0]], [values[1]]
    return ins, outs


def _yaml_scalar(value) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value)
    return text if re.match(r"^[A-Za-z0-9_.\-/]+$", text) else f'"{text}"'


def _yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


def format_report(result: Discovery) -> str:
    """Human-readable summary of what discovery found."""
    lines = [f"Tables inspected: {len(result.tables)}", ""]
    for role, label in (
        ("employees", "Employee master"),
        ("punches", "Punch log"),
        ("leave", "Leave register"),
        ("holidays", "Holiday master"),
    ):
        candidates = getattr(result, role)
        if not candidates:
            lines.append(f"{label:<18} not found")
            continue
        best = candidates[0]
        rows = f"{best.table.row_count:,} rows" if best.table.row_count else "row count n/a"
        lines.append(f"{label:<18} {best.name}  ({rows}, score {best.score})")
        for reason in best.reasons:
            lines.append(f"                   - {reason}")
        for other in candidates[1:3]:
            lines.append(f"                   alternative: {other.name} "
                         f"(score {other.score})")
    if result.flattened:
        lines.append("")
        lines.append("Pre-flattened summary tables (a fixed in/out limit lives here):")
        for table in result.flattened:
            lines.append(f"  {table.name}: {table.flattened_slots} fixed punch slot columns")
    if result.direction_values:
        lines.append("")
        lines.append("Direction values sampled: "
                     + ", ".join(repr(v) for v in result.direction_values))
    return "\n".join(lines)


# ------------------------------------------------------- discovery from a dump
# A .sql dump carries the same catalogue information as a live connection, so
# the mapping can be produced on a machine that has the file but no database —
# which is the usual situation when a dump has been copied off the Matrix server.

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[^\s(]+)\s*\((?P<body>.*?)\)\s*"
    r"(?:ENGINE|DEFAULT\s+CHARSET|ON\s+\[?PRIMARY|WITH\s*\(|;|GO\b)",
    re.IGNORECASE | re.DOTALL,
)

_CONSTRAINT_START = re.compile(
    r"^\s*(CONSTRAINT|PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|KEY|INDEX|CHECK|"
    r"FULLTEXT|SPATIAL|PERIOD\s+FOR)\b",
    re.IGNORECASE,
)

_INSERT_RE = re.compile(
    r"INSERT\s+(?:INTO\s+)?(?P<name>[^\s(]+)", re.IGNORECASE
)


def _strip_identifier(raw: str) -> str:
    """Reduce ``[dbo].[mx_att_log]`` or `` `db`.`log` `` to ``mx_att_log``."""
    cleaned = raw.strip().rstrip(";")
    part = cleaned.split(".")[-1]
    return part.strip().strip("[]`\"'")


def _split_columns(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas that are not inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    in_string = False
    for char in body:
        if char == "'":
            in_string = not in_string
        if not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def parse_sql_dump(text: str) -> list[TableInfo]:
    """Extract tables and columns from the CREATE TABLE statements in a dump."""
    tables: list[TableInfo] = []
    seen: set[str] = set()

    for match in _CREATE_TABLE_RE.finditer(text):
        name = _strip_identifier(match.group("name"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        columns: list[ColumnInfo] = []
        for definition in _split_columns(match.group("body")):
            definition = definition.strip()
            if not definition or _CONSTRAINT_START.match(definition):
                continue
            tokens = definition.split()
            if len(tokens) < 2:
                continue
            column_name = tokens[0].strip("[]`\"'")
            if not column_name or not re.match(r"^[A-Za-z_@#]", column_name):
                continue
            data_type = tokens[1].strip("[]`\"'").split("(")[0].lower()
            columns.append(ColumnInfo(column_name, data_type))

        if columns:
            tables.append(TableInfo(name, columns))

    if tables:
        _attach_insert_counts(text, tables)
    return tables


def _attach_insert_counts(text: str, tables: list[TableInfo]) -> None:
    """Use INSERT statement counts as a stand-in for row counts.

    A dump that carries data lets us tell the raw punch log (many rows per
    employee per day) from a daily summary (one row per employee per day),
    which is the distinction that matters most. Dumps of schema only leave the
    counts at ``None`` and scoring falls back to names and column shapes.
    """
    counts: dict[str, int] = {}
    for match in _INSERT_RE.finditer(text):
        name = _strip_identifier(match.group("name")).lower()
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return
    for table in tables:
        found = counts.get(table.name.lower())
        if found:
            table.row_count = found


def discover_from_sql(text: str) -> Discovery:
    """Run the same role scoring over a dump file instead of a connection."""
    tables = parse_sql_dump(text)
    result = Discovery(tables=tables)

    result.employees = sorted(
        (c for c in (score_employee_table(t) for t in tables) if c),
        key=lambda c: -c.score,
    )
    employee_columns = set()
    if result.employees:
        employee_columns = {
            column.name.upper() for column in result.employees[0].table.columns
        }
    result.punches = sorted(
        (c for c in (score_punch_table(t, employee_columns) for t in tables) if c),
        key=lambda c: -c.score,
    )
    result.leave = sorted(
        (c for c in (score_leave_table(t) for t in tables) if c), key=lambda c: -c.score
    )
    result.holidays = sorted(
        (c for c in (score_holiday_table(t) for t in tables) if c),
        key=lambda c: -c.score,
    )
    result.flattened = [table for table in tables if table.flattened_slots >= 2]

    best = result.best("punches")
    if best:
        result.split_datetime = "timestamp" not in best.roles
        if "direction" in best.roles:
            result.direction_values = _sample_direction_from_inserts(
                text, best.name, best.table, best.roles["direction"]
            )
    return result


def _sample_direction_from_inserts(
    text: str, table_name: str, table: TableInfo, direction_column: str
) -> list[str]:
    """Pull the direction column's values out of the dump's INSERT rows."""
    try:
        position = [column.name for column in table.columns].index(direction_column)
    except ValueError:
        return []

    pattern = re.compile(
        r"INSERT\s+(?:INTO\s+)?[^\s(]*\b"
        + re.escape(table_name)
        + r"\b[^\s(]*\s*(?:\([^)]*\))?\s*VALUES\s*\((?P<values>[^)]*)\)",
        re.IGNORECASE,
    )
    found: set[str] = set()
    for count, match in enumerate(pattern.finditer(text)):
        if count > 2000 or len(found) > 12:
            break
        values = _split_columns(match.group("values"))
        if position >= len(values):
            continue
        value = _unquote_sql_literal(values[position])
        if value and value.upper() != "NULL":
            found.add(value)
    return sorted(found)


def _unquote_sql_literal(raw: str) -> str:
    """Strip SQL quoting from a literal, including T-SQL's ``N'...'`` prefix."""
    value = raw.strip()
    match = re.match(r"^[NnBbXx]?'(?P<body>.*)'$", value, re.DOTALL)
    if match:
        return match.group("body").replace("''", "'")
    return value.strip('"')
