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
