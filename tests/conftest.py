"""Shared fixtures: a synthetic office day built to stress the six-punch limit."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from matrixreports.config import Config
from matrixreports.datasource import InMemoryPunchSource
from matrixreports.model import Direction, Employee, Punch

DAY = date(2026, 6, 1)


def at(clock: str, day: date = DAY) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(day, time(hour, minute))


def punches(emp_id: str, clocks: list[str], day: date = DAY) -> list[Punch]:
    """Alternating IN/OUT punches at the given times."""
    return [
        Punch(emp_id, at(clock, day), Direction.IN if index % 2 == 0 else Direction.OUT)
        for index, clock in enumerate(clocks)
    ]


# A day with 12 sessions / 11 breaks: the case the stock report cannot express.
HEAVY_DAY = [
    "09:44", "09:56", "10:02", "11:20", "11:23", "12:27", "12:32", "13:40",
    "13:44", "14:21", "14:47", "15:39", "15:43", "16:30", "16:35", "17:10",
    "17:14", "17:50", "17:55", "18:20", "18:25", "18:50", "18:55", "19:09",
]

# A quiet day with 2 breaks, well inside the old limit.
LIGHT_DAY = ["10:05", "12:00", "12:30", "15:00", "15:20", "19:04"]


@pytest.fixture
def config() -> Config:
    return Config.from_dict(
        {
            "company": {"name": "Test Co"},
            "shift": {
                "start": "10:00",
                "end": "19:00",
                "late_in_grace": "00:10",
                "late_out_grace": "00:10",
                "weekly_off_days": [4],
            },
            "report": {"min_inout_groups": 5, "max_inout_groups": 0},
            "leave_codes": {"SL": "Sick Leave", "AL": "Annual Leave"},
        }
    )


@pytest.fixture
def employees() -> list[Employee]:
    return [
        Employee("1", "Heavy Walker", code="E1", department="Supply Chain"),
        Employee("2", "Light Walker", code="E2", department="IT"),
        Employee("3", "On Leave", code="E3", department="Treasury"),
    ]


@pytest.fixture
def source(employees) -> InMemoryPunchSource:
    return InMemoryPunchSource(
        employees=employees,
        punches={"1": punches("1", HEAVY_DAY), "2": punches("2", LIGHT_DAY)},
        leave={("3", DAY): "SL"},
    )


@pytest.fixture
def sqlite_db(tmp_path: Path, employees) -> Path:
    """A real database, so the generated SQL is exercised rather than mocked."""
    path = tmp_path / "matrix.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE EmployeeMaster (EmployeeID TEXT, EmployeeCode TEXT, "
        "EmployeeName TEXT, Department TEXT, Designation TEXT, ShiftCode TEXT, IsActive INT)"
    )
    connection.execute(
        "CREATE TABLE AttendanceLog (EmployeeID TEXT, PunchDateTime TEXT, "
        "InOutFlag TEXT, DoorName TEXT)"
    )
    connection.execute(
        "CREATE TABLE LeaveRegister (EmployeeID TEXT, LeaveFrom TEXT, LeaveTo TEXT, "
        "LeaveType TEXT, Status TEXT)"
    )
    connection.execute("CREATE TABLE HolidayMaster (HolidayDate TEXT, HolidayName TEXT)")

    for employee in employees:
        connection.execute(
            "INSERT INTO EmployeeMaster VALUES (?,?,?,?,?,?,1)",
            (employee.emp_id, employee.code, employee.name, employee.department, "", ""),
        )
    for emp_id, clocks in (("1", HEAVY_DAY), ("2", LIGHT_DAY)):
        for index, clock in enumerate(clocks):
            connection.execute(
                "INSERT INTO AttendanceLog VALUES (?,?,?,?)",
                (emp_id, at(clock).strftime("%Y-%m-%d %H:%M:%S"),
                 "1" if index % 2 == 0 else "2", "Main Door"),
            )
    connection.execute(
        "INSERT INTO LeaveRegister VALUES ('3','2026-06-01','2026-06-01','SL','APPROVED')"
    )
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def sqlite_config(sqlite_db: Path) -> Config:
    return Config.from_dict(
        {
            "database": {"driver": "sqlite", "path": str(sqlite_db)},
            "schema": {
                "employees": {
                    "table": "EmployeeMaster",
                    "columns": {
                        "id": "EmployeeID", "code": "EmployeeCode",
                        "name": "EmployeeName", "department": "Department",
                        "designation": "Designation", "shift": "ShiftCode",
                    },
                    "where": "IsActive = 1",
                },
                "punches": {
                    "table": "AttendanceLog",
                    "columns": {
                        "emp_id": "EmployeeID", "timestamp": "PunchDateTime",
                        "direction": "InOutFlag", "device": "DoorName",
                    },
                },
                "leave": {
                    "table": "LeaveRegister",
                    "columns": {
                        "emp_id": "EmployeeID", "date_from": "LeaveFrom",
                        "date_to": "LeaveTo", "code": "LeaveType",
                    },
                    "where": "Status = 'APPROVED'",
                },
                "holidays": {
                    "table": "HolidayMaster",
                    "columns": {"date": "HolidayDate", "name": "HolidayName"},
                },
                "direction_in": ["1"],
                "direction_out": ["2"],
            },
            "shift": {"start": "10:00", "end": "19:00", "weekly_off_days": [4]},
            "leave_codes": {"SL": "Sick Leave"},
        }
    )


# --- web app -------------------------------------------------------------------

@pytest.fixture
def web_config_file(tmp_path, sqlite_db):
    import yaml
    path = tmp_path / "web.yaml"
    path.write_text(yaml.safe_dump({
        "company": {"name": "Test Co"},
        "database": {"driver": "sqlite", "path": str(sqlite_db)},
        "schema": {
            "employees": {"table": "EmployeeMaster",
                          "columns": {"id": "EmployeeID", "code": "EmployeeCode",
                                      "name": "EmployeeName", "department": "Department"},
                          "where": "IsActive = 1"},
            "punches": {"table": "AttendanceLog",
                        "columns": {"emp_id": "EmployeeID", "timestamp": "PunchDateTime",
                                    "direction": "InOutFlag", "device": "DoorName"}},
            "direction_in": ["1"], "direction_out": ["2"],
        },
        "shift": {"start": "10:00", "end": "19:00", "weekly_off_days": [4]},
    }), encoding="utf-8")
    return path


@pytest.fixture
def client(web_config_file):
    pytest.importorskip("flask")
    from webapp.app import app as flask_app
    flask_app.config.update(TESTING=True, MATRIX_CONFIG=str(web_config_file))
    with flask_app.test_client() as test_client:
        yield test_client
