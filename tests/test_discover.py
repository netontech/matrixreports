"""Schema discovery against COSEC-shaped databases."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from matrixreports.config import Config
from matrixreports.datasource import SqlPunchSource
from matrixreports.discover import discover, format_report, render_config


def make_db(tmp_path: Path, script: str, name: str = "cosec.db") -> sqlite3.Connection:
    path = tmp_path / name
    connection = sqlite3.connect(path)
    connection.executescript(script)
    connection.commit()
    return connection


COSEC_LIKE = """
CREATE TABLE mx_employee_master (
    EmployeeID INTEGER, EmployeeCode TEXT, EmployeeName TEXT,
    Department TEXT, Designation TEXT, ShiftCode TEXT);
CREATE TABLE mx_attendance_log (
    EmployeeID INTEGER, PunchDateTime TEXT, InOutFlag TEXT, DoorName TEXT);
CREATE TABLE mx_leave_register (
    EmployeeID INTEGER, LeaveFrom TEXT, LeaveTo TEXT, LeaveType TEXT);
CREATE TABLE mx_holiday_master (HolidayDate TEXT, HolidayName TEXT);
CREATE TABLE mx_device_master (DeviceID INTEGER, DeviceName TEXT);
"""

# The shape that causes a fixed in/out ceiling: six slot pairs and no more.
FLATTENED = """
CREATE TABLE mx_daily_attendance (
    EmployeeID INTEGER, AttDate TEXT,
    IN1 TEXT, OUT1 TEXT, IN2 TEXT, OUT2 TEXT, IN3 TEXT, OUT3 TEXT,
    IN4 TEXT, OUT4 TEXT, IN5 TEXT, OUT5 TEXT, IN6 TEXT, OUT6 TEXT,
    TotalHours REAL);
"""


# The real Matrix table, which numbers its slots instead of naming them by
# direction.  Twelve slots == 1st In + five (OUT, IN) pairs + Last Out, which is
# exactly the six-group layout of the stock report.  The SPFID*/P*TYPE columns
# sit alongside them and must not be counted as slots.
MATRIX_FLATTENED = """
CREATE TABLE mx_datdtrn (
    UserID TEXT, PDate TEXT,
    Punch1 TEXT, Punch2 TEXT, Punch3 TEXT, Punch4 TEXT,
    Punch5 TEXT, Punch6 TEXT, Punch7 TEXT, Punch8 TEXT,
    Punch9 TEXT, Punch10 TEXT, Punch11 TEXT, Punch12 TEXT,
    SPFID1 INTEGER, SPFID2 INTEGER, SPFID12 INTEGER,
    P1TYPE INTEGER, P1MID INTEGER, P1DID INTEGER,
    Perstime1 REAL, DPTID1 INTEGER, RINId1 INTEGER,
    OutPunch TEXT, WorkTime REAL);
"""


def test_finds_each_role_in_a_cosec_shaped_database(tmp_path):
    connection = make_db(tmp_path, COSEC_LIKE)
    result = discover(connection, "sqlite")

    assert result.best("employees").name == "mx_employee_master"
    assert result.best("punches").name == "mx_attendance_log"
    assert result.best("leave").name == "mx_leave_register"
    assert result.best("holidays").name == "mx_holiday_master"

    roles = result.best("punches").roles
    assert roles["emp_id"] == "EmployeeID"
    assert roles["timestamp"] == "PunchDateTime"
    assert roles["direction"] == "InOutFlag"
    assert roles["device"] == "DoorName"


def test_flattened_summary_table_is_rejected_and_reported(tmp_path):
    """The pre-flattened table is where a six-punch ceiling comes from."""
    connection = make_db(tmp_path, COSEC_LIKE + FLATTENED)
    result = discover(connection, "sqlite")

    assert [table.name for table in result.flattened] == ["mx_daily_attendance"]
    assert result.flattened[0].flattened_slots == 12      # IN1..IN6 + OUT1..OUT6

    # It must never win over the raw log.
    assert result.best("punches").name == "mx_attendance_log"
    flattened = next(
        (c for c in result.punches if c.name == "mx_daily_attendance"), None
    )
    if flattened is not None:
        assert flattened.score < result.best("punches").score
        assert any("REJECTED" in reason for reason in flattened.reasons)

    assert "mx_daily_attendance" in format_report(result)
    assert "pre-flattened" in render_config(result).lower()


def test_flattened_table_alone_is_not_selected_as_the_punch_source(tmp_path):
    connection = make_db(tmp_path, FLATTENED)
    result = discover(connection, "sqlite")
    best = result.best("punches")
    # Either nothing is chosen, or the choice is flagged as rejected.
    assert best is None or any("REJECTED" in reason for reason in best.reasons)


def test_matrix_punch_numbered_summary_is_detected(tmp_path):
    """Matrix names its slots Punch1..Punch12, not IN1/OUT1.

    Regression test: the slot pattern only matched direction-named columns, so
    on a real COSEC database the pre-flattened table -- the very thing the
    warning exists for -- was never reported.
    """
    connection = make_db(tmp_path, COSEC_LIKE + MATRIX_FLATTENED)
    result = discover(connection, "sqlite")

    assert [table.name for table in result.flattened] == ["mx_datdtrn"]
    # Exactly the twelve Punch* columns; SPFID*, P1TYPE, Perstime1, DPTID1,
    # RINId1 and OutPunch must not inflate the count.
    assert result.flattened[0].flattened_slots == 12

    assert result.best("punches").name == "mx_attendance_log"
    assert "mx_datdtrn" in format_report(result)
    assert "pre-flattened" in render_config(result).lower()


def test_matrix_numbered_summary_never_wins_over_the_raw_log(tmp_path):
    connection = make_db(tmp_path, MATRIX_FLATTENED)
    result = discover(connection, "sqlite")
    best = result.best("punches")
    assert best is None or any("REJECTED" in reason for reason in best.reasons)


def test_a_raw_punch_log_is_not_mistaken_for_a_flattened_table(tmp_path):
    """Guard the widened pattern against false positives."""
    connection = make_db(tmp_path, COSEC_LIKE)
    result = discover(connection, "sqlite")
    assert result.flattened == []
    assert result.best("punches").name == "mx_attendance_log"


def test_split_date_and_time_columns_are_detected(tmp_path):
    connection = make_db(
        tmp_path,
        """
        CREATE TABLE EmployeeMaster (EmployeeID INTEGER, EmployeeName TEXT);
        CREATE TABLE AttendanceLog (
            EmployeeID INTEGER, PunchDate DATE, PunchTime TIME, InOutFlag TEXT);
        """,
    )
    result = discover(connection, "sqlite")
    roles = result.best("punches").roles
    assert roles["date"] == "PunchDate"
    assert roles["time"] == "PunchTime"
    assert "timestamp" not in roles
    assert result.split_datetime is True
    assert "punch_datetime_is_split: true" in render_config(result)


def test_direction_values_are_sampled_and_split(tmp_path):
    connection = make_db(tmp_path, COSEC_LIKE)
    connection.execute(
        "INSERT INTO mx_attendance_log VALUES (1, '2026-06-01 10:00:00', 'I', 'Door')"
    )
    connection.execute(
        "INSERT INTO mx_attendance_log VALUES (1, '2026-06-01 19:00:00', 'O', 'Door')"
    )
    connection.commit()
    result = discover(connection, "sqlite")
    assert result.direction_values == ["I", "O"]
    text = render_config(result)
    assert 'direction_in:  ["I"]' in text
    assert 'direction_out: ["O"]' in text
    assert "direction_mode: device" in text


def test_no_direction_column_falls_back_to_alternation(tmp_path):
    connection = make_db(
        tmp_path,
        """
        CREATE TABLE EmployeeMaster (EmployeeID INTEGER, EmployeeName TEXT);
        CREATE TABLE AttendanceLog (EmployeeID INTEGER, PunchDateTime TEXT);
        """,
    )
    result = discover(connection, "sqlite")
    assert "direction" not in result.best("punches").roles
    assert "direction_mode: alternate" in render_config(result)


def test_generated_config_is_valid_and_drives_a_real_report(tmp_path, sqlite_db):
    """The draft config must load and run without hand editing."""
    import yaml

    connection = sqlite3.connect(sqlite_db)
    result = discover(connection, "sqlite")
    connection.close()

    text = render_config(
        result, database={"driver": "sqlite", "path": str(sqlite_db)}
    )
    parsed = yaml.safe_load(text)
    assert parsed["schema"]["punches"]["table"] == "AttendanceLog"

    config = Config.from_dict(parsed)
    with SqlPunchSource(config) as source:
        punches = source.punches(__import__("datetime").date(2026, 6, 1),
                                 __import__("datetime").date(2026, 6, 1))
    assert len(punches["1"]) == 24


def test_unrelated_database_reports_nothing_found(tmp_path):
    connection = make_db(
        tmp_path,
        "CREATE TABLE invoices (InvoiceID INTEGER, Amount REAL, IssuedOn TEXT);",
    )
    result = discover(connection, "sqlite")
    assert result.best("employees") is None
    assert "not found" in format_report(result)


def test_row_counts_are_read_for_sqlite(tmp_path):
    connection = make_db(tmp_path, COSEC_LIKE)
    for index in range(5):
        connection.execute(
            "INSERT INTO mx_employee_master VALUES (?,?,?,?,?,?)",
            (index, f"E{index}", f"Name {index}", "IT", "", ""),
        )
    connection.commit()
    result = discover(connection, "sqlite")
    assert result.best("employees").table.row_count == 5


# --------------------------------------------------------------- dump parsing
TSQL_DUMP = """
USE [COSEC]
GO
SET ANSI_NULLS ON
GO
CREATE TABLE [dbo].[mx_employee_master](
\t[EmployeeID] [int] IDENTITY(1,1) NOT NULL,
\t[EmployeeCode] [varchar](20) NOT NULL,
\t[EmployeeName] [varchar](100) NULL,
\t[Department] [varchar](50) NULL,
\t[IsActive] [bit] NOT NULL,
 CONSTRAINT [PK_emp] PRIMARY KEY CLUSTERED ([EmployeeID] ASC)
) ON [PRIMARY]
GO
CREATE TABLE [dbo].[mx_attendance_log](
\t[LogID] [bigint] IDENTITY(1,1) NOT NULL,
\t[EmployeeID] [int] NOT NULL,
\t[PunchDateTime] [datetime] NOT NULL,
\t[InOutFlag] [char](1) NULL,
\t[DoorName] [varchar](50) NULL,
 CONSTRAINT [PK_log] PRIMARY KEY CLUSTERED ([LogID] ASC)
) ON [PRIMARY]
GO
CREATE TABLE [dbo].[mx_daily_attendance](
\t[EmployeeID] [int] NOT NULL,
\t[AttDate] [date] NOT NULL,
\t[IN1] [datetime] NULL, [OUT1] [datetime] NULL,
\t[IN2] [datetime] NULL, [OUT2] [datetime] NULL,
\t[IN3] [datetime] NULL, [OUT3] [datetime] NULL,
\t[IN4] [datetime] NULL, [OUT4] [datetime] NULL,
\t[IN5] [datetime] NULL, [OUT5] [datetime] NULL,
\t[IN6] [datetime] NULL, [OUT6] [datetime] NULL,
\t[TotalHours] [decimal](5, 2) NULL
) ON [PRIMARY]
GO
CREATE TABLE [dbo].[mx_leave_register](
\t[EmployeeID] [int] NOT NULL,
\t[LeaveFrom] [date] NOT NULL,
\t[LeaveTo] [date] NOT NULL,
\t[LeaveType] [varchar](10) NULL
) ON [PRIMARY]
GO
INSERT [dbo].[mx_attendance_log] ([LogID],[EmployeeID],[PunchDateTime],[InOutFlag],[DoorName]) VALUES (1, 1, N'2026-06-01 09:44:00', N'I', N'Main Door')
GO
INSERT [dbo].[mx_attendance_log] ([LogID],[EmployeeID],[PunchDateTime],[InOutFlag],[DoorName]) VALUES (2, 1, N'2026-06-01 19:09:00', N'O', N'Main Door')
GO
"""

MYSQL_DUMP = """
DROP TABLE IF EXISTS `employee_master`;
CREATE TABLE `employee_master` (
  `emp_id` int(11) NOT NULL AUTO_INCREMENT,
  `emp_name` varchar(100) DEFAULT NULL,
  `dept` varchar(50) DEFAULT NULL,
  PRIMARY KEY (`emp_id`),
  KEY `idx_name` (`emp_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
CREATE TABLE `attendance_log` (
  `log_id` bigint(20) NOT NULL AUTO_INCREMENT,
  `emp_id` int(11) NOT NULL,
  `punch_datetime` datetime NOT NULL,
  `in_out` tinyint(4) DEFAULT NULL,
  PRIMARY KEY (`log_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
"""


def test_parses_tsql_dump_into_tables_and_columns():
    from matrixreports.discover import parse_sql_dump

    tables = {table.name: table for table in parse_sql_dump(TSQL_DUMP)}
    assert set(tables) == {
        "mx_employee_master", "mx_attendance_log",
        "mx_daily_attendance", "mx_leave_register",
    }
    columns = [column.name for column in tables["mx_attendance_log"].columns]
    assert columns == ["LogID", "EmployeeID", "PunchDateTime", "InOutFlag", "DoorName"]
    # Constraint lines must not become columns.
    assert "CONSTRAINT" not in columns
    assert tables["mx_attendance_log"].columns[2].is_datetime


def test_discovers_roles_from_a_tsql_dump():
    from matrixreports.discover import discover_from_sql

    result = discover_from_sql(TSQL_DUMP)
    assert result.best("employees").name == "mx_employee_master"
    assert result.best("punches").name == "mx_attendance_log"
    assert result.best("leave").name == "mx_leave_register"
    assert result.best("punches").roles["timestamp"] == "PunchDateTime"


def test_flattened_table_in_a_dump_is_flagged_and_rejected():
    from matrixreports.discover import discover_from_sql

    result = discover_from_sql(TSQL_DUMP)
    assert [t.name for t in result.flattened] == ["mx_daily_attendance"]
    assert result.flattened[0].flattened_slots == 12
    assert result.best("punches").name == "mx_attendance_log"
    assert "pre-flattened" in render_config(result).lower()


def test_direction_values_are_read_from_insert_rows():
    """T-SQL N'...' literals must be unwrapped, not taken literally."""
    from matrixreports.discover import discover_from_sql

    result = discover_from_sql(TSQL_DUMP)
    assert result.direction_values == ["I", "O"]
    text = render_config(result)
    assert 'direction_in:  ["I"]' in text
    assert 'direction_out: ["O"]' in text


def test_insert_counts_stand_in_for_row_counts():
    from matrixreports.discover import discover_from_sql

    result = discover_from_sql(TSQL_DUMP)
    assert result.best("punches").table.row_count == 2


def test_parses_mysql_backtick_dump():
    from matrixreports.discover import discover_from_sql, parse_sql_dump

    tables = {table.name: table for table in parse_sql_dump(MYSQL_DUMP)}
    assert set(tables) == {"employee_master", "attendance_log"}
    columns = [column.name for column in tables["attendance_log"].columns]
    assert columns == ["log_id", "emp_id", "punch_datetime", "in_out"]

    result = discover_from_sql(MYSQL_DUMP)
    assert result.best("punches").name == "attendance_log"
    assert result.best("punches").roles["emp_id"] == "emp_id"


def test_schema_only_dump_has_no_row_counts():
    from matrixreports.discover import discover_from_sql

    result = discover_from_sql(MYSQL_DUMP)
    assert result.best("punches").table.row_count is None
    assert result.direction_values == []


def test_dump_with_no_create_table_yields_nothing():
    from matrixreports.discover import parse_sql_dump

    assert parse_sql_dump("SELECT 1;\nGO\n") == []


def test_unquote_handles_tsql_and_escaped_quotes():
    from matrixreports.discover import _unquote_sql_literal

    assert _unquote_sql_literal("N'IN'") == "IN"
    assert _unquote_sql_literal("'OUT'") == "OUT"
    assert _unquote_sql_literal("  1  ") == "1"
    assert _unquote_sql_literal("N'O''Brien'") == "O'Brien"
