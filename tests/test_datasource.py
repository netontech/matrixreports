"""Data sources: the generated SQL runs against a real database."""

from datetime import date

import pytest

from matrixreports.builder import AttendanceBuilder
from matrixreports.datasource import CsvPunchSource, SqlPunchSource, open_source
from matrixreports.duration import hhmm
from matrixreports.model import Direction

from conftest import DAY, HEAVY_DAY, at


def test_sql_source_reads_employees_with_the_mapped_columns(sqlite_config):
    with SqlPunchSource(sqlite_config) as source:
        employees = source.employees()
    by_name = {employee.name: employee for employee in employees}
    assert set(by_name) == {"Heavy Walker", "Light Walker", "On Leave"}
    assert by_name["Heavy Walker"].department == "Supply Chain"
    assert by_name["Light Walker"].code == "E2"


def test_sql_source_reads_every_punch_not_just_the_first_six(sqlite_config):
    with SqlPunchSource(sqlite_config) as source:
        punches = source.punches(DAY, DAY)
    assert len(punches["1"]) == len(HEAVY_DAY) == 24
    assert punches["1"][0].direction == Direction.IN
    assert punches["1"][1].direction == Direction.OUT
    assert punches["1"][0].device == "Main Door"


def test_sql_source_reads_leave_register(sqlite_config):
    with SqlPunchSource(sqlite_config) as source:
        leave = source.leave(DAY, DAY)
    assert leave == {("3", DAY): "SL"}


def test_end_to_end_through_sqlite(sqlite_config):
    with open_source(sqlite_config) as source:
        book = AttendanceBuilder(sqlite_config, source).build(DAY, DAY)
    record = book.record("1", DAY)
    assert record.break_count == 11
    assert hhmm(record.worked) == "08:13"
    assert book.record("3", DAY).status == "Sick Leave"


def test_widened_window_catches_punches_either_side_of_midnight(sqlite_config):
    """The query pads the range by a day so night shifts are not cut in half."""
    with SqlPunchSource(sqlite_config) as source:
        punches = source.punches(date(2026, 6, 2), date(2026, 6, 2))
    assert punches["1"]          # 1 June punches are visible to the 2 June query


def test_unmapped_column_fails_with_a_useful_message(sqlite_config):
    sqlite_config.schema.punches.columns.pop("timestamp")
    with SqlPunchSource(sqlite_config) as source:
        with pytest.raises(KeyError, match="timestamp"):
            source.punches(DAY, DAY)


def test_csv_extract_source(tmp_path):
    path = tmp_path / "punches.csv"
    lines = ["emp_id,name,department,timestamp,direction"]
    for index, clock in enumerate(HEAVY_DAY):
        lines.append(
            f"1,Heavy Walker,Supply Chain,{at(clock):%Y-%m-%d %H:%M:%S},"
            f"{'IN' if index % 2 == 0 else 'OUT'}"
        )
    path.write_text("\n".join(lines), encoding="utf-8")

    source = CsvPunchSource(path)
    assert [employee.name for employee in source.employees()] == ["Heavy Walker"]
    assert len(source.punches(DAY, DAY)["1"]) == 24


def test_csv_extract_with_split_date_and_time(tmp_path):
    path = tmp_path / "split.csv"
    path.write_text(
        "emp_id,name,date,time\n"
        "1,Heavy Walker,2026-06-01,10:00:00\n"
        "1,Heavy Walker,2026-06-01,19:00:00\n",
        encoding="utf-8",
    )
    punches = CsvPunchSource(path).punches(DAY, DAY)
    assert [punch.timestamp.hour for punch in punches["1"]] == [10, 19]
