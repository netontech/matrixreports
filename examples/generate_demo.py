"""Build a synthetic Matrix-shaped database and render all four reports.

Run this to see the output without needing access to the live server:

    python examples/generate_demo.py --out out/demo

The generated data deliberately includes staff who step out eight, ten and
thirteen times a day — the cases the stock Matrix report cannot represent.
All names are invented.
"""

from __future__ import annotations

import argparse
import calendar
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from matrixreports.builder import AttendanceBuilder
from matrixreports.config import Config
from matrixreports.datasource import SqlPunchSource
from matrixreports.excel import write_workbook
from matrixreports.reports import (
    build_daily_report,
    build_monthly_report,
    build_summary_report,
    build_weekly_report,
)

STAFF = [
    ("Adeel Rahman", "Supply Chain", 5),
    ("Bianca Torres", "Treasury", 2),
    ("Chandra Iyer", "IT", 13),      # the pathological case: 13 breaks a day
    ("Dmitri Volkov", "Logistics", 3),
    ("Elena Costa", "Treasury", 8),  # beyond the old limit
    ("Farid Haddad", "Supply Chain", 10),
    ("Grace Mwangi", "IT", 1),
    ("Hassan Ali", "Logistics", 6),  # just beyond the old limit
    ("Ingrid Larsen", "Treasury", 4),
    ("Jomar Reyes", "Supply Chain", 0),
]

LEAVE = {("3", 12): "AL", ("7", 9): "SL"}


def build_database(path: Path, month: date, seed: int = 7) -> None:
    random.seed(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE EmployeeMaster (EmployeeID TEXT, EmployeeCode TEXT,
            EmployeeName TEXT, Department TEXT, IsActive INT);
        CREATE TABLE AttendanceLog (EmployeeID TEXT, PunchDateTime TEXT,
            InOutFlag TEXT, DoorName TEXT);
        CREATE TABLE LeaveRegister (EmployeeID TEXT, LeaveFrom TEXT, LeaveTo TEXT,
            LeaveType TEXT, Status TEXT);
        CREATE TABLE HolidayMaster (HolidayDate TEXT, HolidayName TEXT);
        """
    )

    for index, (name, department, _) in enumerate(STAFF, start=1):
        connection.execute(
            "INSERT INTO EmployeeMaster VALUES (?,?,?,?,1)",
            (str(index), f"E{index:03d}", name, department),
        )

    days_in_month = calendar.monthrange(month.year, month.month)[1]
    for day_number in range(1, days_in_month + 1):
        day = month.replace(day=day_number)
        if day.weekday() == 4:                     # Friday is the weekly off
            continue
        for index, (_, _, breaks) in enumerate(STAFF, start=1):
            if (str(index), day_number) in LEAVE:
                continue
            if random.random() < 0.04:             # the odd unexplained absence
                continue
            for stamp, flag in day_punches(day, breaks):
                connection.execute(
                    "INSERT INTO AttendanceLog VALUES (?,?,?,?)",
                    (str(index), stamp.strftime("%Y-%m-%d %H:%M:%S"), flag, "Main Door"),
                )

    for (emp_id, day_number), code in LEAVE.items():
        stamp = month.replace(day=day_number).isoformat()
        connection.execute(
            "INSERT INTO LeaveRegister VALUES (?,?,?,?,'APPROVED')",
            (emp_id, stamp, stamp, code),
        )
    connection.execute(
        "INSERT INTO HolidayMaster VALUES (?, 'Public Holiday')",
        (month.replace(day=min(15, days_in_month)).isoformat(),)
    )
    connection.commit()
    connection.close()


def day_punches(day: date, breaks: int) -> list[tuple[datetime, str]]:
    """One employee's punches for one day: in, `breaks` step-outs, then out."""
    first_in = datetime.combine(day, datetime.min.time()) + timedelta(
        hours=9, minutes=random.randint(35, 95)
    )
    punches = [(first_in, "1")]
    cursor = first_in
    for _ in range(breaks):
        cursor += timedelta(minutes=random.randint(25, 70))
        out = cursor
        cursor += timedelta(minutes=random.choice([2, 3, 4, 6, 12, 25, 40]))
        if cursor.hour >= 19:
            break
        punches.append((out, "2"))
        punches.append((cursor, "1"))
    last_out = max(
        cursor + timedelta(minutes=random.randint(20, 90)),
        datetime.combine(day, datetime.min.time()) + timedelta(hours=19),
    )
    punches.append((last_out, "2"))
    return punches


def demo_config(database: Path) -> Config:
    return Config.from_dict(
        {
            "company": {"name": "Demo Trading L.L.C"},
            "database": {"driver": "sqlite", "path": str(database)},
            "schema": {
                "employees": {
                    "table": "EmployeeMaster",
                    "columns": {"id": "EmployeeID", "code": "EmployeeCode",
                                "name": "EmployeeName", "department": "Department"},
                    "where": "IsActive = 1",
                },
                "punches": {
                    "table": "AttendanceLog",
                    "columns": {"emp_id": "EmployeeID", "timestamp": "PunchDateTime",
                                "direction": "InOutFlag", "device": "DoorName"},
                },
                "leave": {
                    "table": "LeaveRegister",
                    "columns": {"emp_id": "EmployeeID", "date_from": "LeaveFrom",
                                "date_to": "LeaveTo", "code": "LeaveType"},
                    "where": "Status = 'APPROVED'",
                },
                "holidays": {
                    "table": "HolidayMaster",
                    "columns": {"date": "HolidayDate", "name": "HolidayName"},
                },
                "direction_in": ["1"],
                "direction_out": ["2"],
            },
            "shift": {"start": "10:00", "end": "19:00", "late_in_grace": "00:10",
                      "late_out_grace": "00:10", "weekly_off_days": [4]},
            "leave_codes": {"AL": "Annual Leave", "SL": "Sick Leave"},
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="out/demo", help="output directory")
    parser.add_argument("--month", default="2026-06", help="YYYY-MM")
    args = parser.parse_args()

    month = datetime.strptime(args.month, "%Y-%m").date()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    database = out / "demo_matrix.db"
    build_database(database, month)
    config = demo_config(database)

    days = calendar.monthrange(month.year, month.month)[1]
    with SqlPunchSource(config) as source:
        book = AttendanceBuilder(config, source).build(month, month.replace(day=days))

    first_working_day = next(
        day for day in book.days if book.for_day(day) and
        any(record.has_data for record in book.for_day(day))
    )

    write_workbook(build_daily_report(book, first_working_day), out / "daily.xlsx")
    write_workbook(build_summary_report(book, first_working_day), out / "summary.xlsx",
                   combine=True)
    write_workbook(build_weekly_report(book), out / "weekly.xlsx")
    write_workbook(build_monthly_report(book), out / "monthly.xlsx")

    print(f"demo database: {database}")
    print(f"deepest day in the month: {book.max_punches} punches, {book.max_breaks} breaks")
    print(f"daily report for {first_working_day} uses "
          f"{build_daily_report(book, first_working_day).meta['groups']} OUT/IN groups")
    print(f"workbooks written to {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
