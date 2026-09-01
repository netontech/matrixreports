"""The web front end.

These drive the real pipeline against the sqlite fixture, so a break in the
builder, the reports or the exporters shows up here too. The point of the app is
that the layout is not fixed at five groups, so that is what is asserted.
"""

from __future__ import annotations

import re

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("flask")

from webapp.app import app as flask_app          # noqa: E402


@pytest.fixture
def config_file(tmp_path, sqlite_db):
    path = tmp_path / "web.yaml"
    path.write_text(yaml.safe_dump({
        "company": {"name": "Test Co"},
        "database": {"driver": "sqlite", "path": str(sqlite_db)},
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
            "direction_in": ["1"],
            "direction_out": ["2"],
        },
        "shift": {"start": "10:00", "end": "19:00", "weekly_off_days": [4]},
    }), encoding="utf-8")
    return path


@pytest.fixture
def client(config_file):
    flask_app.config.update(TESTING=True, MATRIX_CONFIG=str(config_file))
    with flask_app.test_client() as test_client:
        yield test_client


def group_numbers(html: str) -> list[str]:
    band = re.search(r'<tr class="groupband">(.*?)</tr>', html, re.S)
    return re.findall(r">(\d+)</th>", band.group(1)) if band else []


def test_landing_page_renders_without_a_date(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Pick a date" in response.data


def test_daily_report_grows_past_the_five_group_ceiling(client):
    """The fixture's heavy day has 11 breaks; the stock report stops at 5."""
    response = client.get("/?report=daily&date=2026-06-01")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert group_numbers(html)[-1] == "11"
    assert html.count("MINS") == 11
    # The banner has to state the gap, since that is the whole argument.
    assert "more than the stock report can show" in html


def test_groups_can_be_pinned_to_the_old_width(client):
    """Pinning to 5 reproduces the legacy layout for side-by-side comparison."""
    html = client.get("/?report=daily&date=2026-06-01&groups=5").get_data(as_text=True)
    assert group_numbers(html)[-1] == "5"
    assert html.count("MINS") == 5


@pytest.mark.parametrize("kind", ["daily", "summary", "weekly", "monthly", "yearly"])
def test_every_report_type_renders(client, kind):
    response = client.get(f"/?report={kind}&date=2026-06-01")
    assert response.status_code == 200
    assert b'<section class="sheet">' in response.data


def test_durations_render_as_hhmm_not_decimals(client):
    """The legacy sheets wrote 8.14 for 8h14m and then summed it. Never that."""
    html = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    cells = re.findall(r'<td class="num">([^<]*)</td>', html)
    times = [c for c in cells if c.strip()]
    assert times, "expected some numeric cells"
    assert not any(re.fullmatch(r"\d+\.\d\d", c) for c in times)


def test_xlsx_download(client):
    response = client.get("/download.xlsx?report=daily&date=2026-06-01")
    assert response.status_code == 200
    assert response.data[:2] == b"PK"                 # a real zip/xlsx
    assert "attachment" in response.headers["Content-Disposition"]


def test_csv_download(client):
    response = client.get("/download.csv?report=daily&date=2026-06-01")
    assert response.status_code == 200
    assert b"Employee Name" in response.data


def test_unknown_download_format_is_rejected(client):
    assert client.get("/download.exe?report=daily&date=2026-06-01").status_code == 404


def test_a_bad_date_falls_back_instead_of_erroring(client):
    assert client.get("/?report=daily&date=nonsense").status_code == 200


def test_a_broken_config_reports_the_problem(client, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("database: {driver: sqlite, path: /nonexistent.db}\n", encoding="utf-8")
    flask_app.config["MATRIX_CONFIG"] = str(bad)
    response = client.get("/?report=daily&date=2026-06-01")
    assert response.status_code == 500
    assert b"Could not build the report" in response.data


def test_a_date_with_no_punches_says_so(client):
    """An empty day must not look like a real report.

    The layout falls back to five groups when there are no breaks, which is
    deliberate - but without this notice an empty day is indistinguishable from
    a working one, which is how a wrong date goes unnoticed.
    """
    response = client.get("/?report=daily&date=2026-07-15")
    assert response.status_code == 200
    assert b"No punches found" in response.data


def test_a_day_with_punches_shows_no_empty_notice(client):
    response = client.get("/?report=daily&date=2026-06-01")
    assert b"No punches found" not in response.data
