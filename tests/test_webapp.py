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


def mins_columns(html: str) -> int:
    """Count MINS *column headers* only.

    Not bare occurrences of the word - the headers also carry a "Sort by MINS"
    tooltip, and counting those double-counts every group.
    """
    return len(re.findall(r"<th[^>]*>MINS<", html))


def test_landing_page_renders_without_a_date(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Choose a report" in response.data


def test_daily_report_grows_past_the_five_group_ceiling(client):
    """The fixture's heavy day has 11 breaks; the stock report stops at 5."""
    response = client.get("/?report=daily&date=2026-06-01")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert group_numbers(html)[-1] == "11"
    assert mins_columns(html) == 11
    # The banner has to state the gap, since that is the whole argument.
    assert "more than the stock report can show" in html


def test_groups_can_be_pinned_to_the_old_width(client):
    """Pinning to 5 reproduces the legacy layout for side-by-side comparison."""
    html = client.get("/?report=daily&date=2026-06-01&groups=5").get_data(as_text=True)
    assert group_numbers(html)[-1] == "5"
    assert mins_columns(html) == 5


@pytest.mark.parametrize("kind", ["daily", "summary", "weekly", "monthly", "yearly"])
def test_every_report_type_renders(client, kind):
    response = client.get(f"/?report={kind}&date=2026-06-01")
    assert response.status_code == 200
    assert b"data-sheet" in response.data


def test_durations_render_as_hhmm_not_decimals(client):
    """The legacy sheets wrote 8.14 for 8h14m and then summed it. Never that."""
    html = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    cells = re.findall(r'<td class="num[^"]*"[^>]*>([^<]*)</td>', html)
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


# --- interface behaviour ---------------------------------------------------

def test_rows_past_the_ceiling_are_marked(client):
    """The row that broke the old report is the one a reader is looking for."""
    html = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    over = re.findall(r'<tr class="[^"]*\bover\b[^"]*"[^>]*>', html)
    assert over, "expected the 11-break day to be flagged"
    assert 'title="11 breaks' in html


def test_quiet_rows_are_not_marked(client):
    """The light day has 2 breaks and must not be flagged."""
    html = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    body = html.split("<tbody>")[1]
    assert len(re.findall(r"<tr", body)) > len(re.findall(r'<tr class="[^"]*\bover\b', body))


def test_identity_columns_are_pinned(client):
    """A 46-column row is unreadable if the name scrolls away."""
    html = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    assert 'pin-1' in html and 'pin-2' in html


def test_columns_are_sortable(client):
    html = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    assert 'class="sortable' in html
    assert 'role="columnheader"' in html
    assert 'tabindex="0"' in html          # sortable by keyboard, not just mouse


@pytest.mark.parametrize("kind,expected", [
    ("daily", "2026-05-31"), ("summary", "2026-05-31"),
    ("weekly", "2026-05-25"), ("monthly", "2026-05-01"), ("yearly", "2025-06-01"),
])
def test_previous_link_steps_in_the_reports_own_units(client, kind, expected):
    """A month report should step a month, not a day."""
    html = client.get(f"/?report={kind}&date=2026-06-01").get_data(as_text=True)
    assert f"date={expected}" in html


def test_filter_and_print_controls_are_present(client):
    html = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    assert 'id="filter"' in html
    assert 'id="print"' in html


# --- period bands (the shape the client's own sheets use) ------------------

def test_weekly_report_bands_columns_by_week_number(client):
    """Their weekly sheet is read by 'Week 23', not by a date range."""
    html = client.get("/?report=weekly&date=2026-06-01").get_data(as_text=True)
    band = re.search(r'<tr class="groupband">(.*?)</tr>', html, re.S)
    assert band, "weekly report should carry a band row"
    assert re.search(r">Week \d+<", band.group(1))


def test_monthly_report_bands_columns_by_weekday(client):
    """Weekends are spotted by day name; the day number alone does not show them."""
    html = client.get("/?report=monthly&date=2026-06-01").get_data(as_text=True)
    band = re.search(r'<tr class="groupband">(.*?)</tr>', html, re.S)
    assert band
    labels = re.findall(r">([A-Z][a-z]{2})</th>", band.group(1))
    assert {"Sat", "Sun"} <= set(labels)


def test_band_spans_cover_every_column_exactly(client):
    """A short band row silently misaligns every header beneath it."""
    for kind in ("daily", "weekly", "monthly"):
        html = client.get(f"/?report={kind}&date=2026-06-01").get_data(as_text=True)
        band = re.search(r'<tr class="groupband">(.*?)</tr>', html, re.S)
        if not band:
            continue
        spans = [int(n) for n in re.findall(r'colspan="(\d+)"', band.group(1))]
        cols = len(re.findall(r"<th[^>]*data-col=", html))
        assert sum(spans) == cols, f"{kind}: band covers {sum(spans)} of {cols} columns"


# --- clearing ---------------------------------------------------------------

def test_clear_button_is_always_present(client):
    """It must be visible before you need it, not appear once you are stuck.

    Hiding it until a selection existed meant the control could not be found
    when someone went looking for it.
    """
    plain = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    assert 'id="clear-selections"' in plain

    selected = client.get("/?report=daily&date=2026-06-01&employee=E1").get_data(as_text=True)
    assert 'id="clear-selections"' in selected


def test_clear_button_knows_whether_the_query_needs_reloading(client):
    """Sorting and filtering clear in place; a selection needs a round trip."""
    plain = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    assert 'data-dirty="0"' in plain
    selected = client.get("/?report=daily&date=2026-06-01&employee=E1").get_data(as_text=True)
    assert 'data-dirty="1"' in selected


def test_clear_link_drops_the_selections_but_keeps_the_report_and_date(client):
    html = client.get("/?report=daily&date=2026-06-01&employee=E1&groups=7").get_data(as_text=True)
    link = re.search(r'id="clear-selections"[^>]*href="([^"]+)"', html)
    assert link, "expected a clear link"
    href = link.group(1)
    assert "report=daily" in href and "date=2026-06-01" in href
    assert "employee=" not in href and "groups=" not in href


def test_filter_has_a_clear_control(client):
    html = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    assert 'id="clear-filter"' in html


def test_weekly_spans_several_weeks_for_comparison(client):
    """One week per page loses the trend, which is the point of their sheet."""
    html = client.get("/?report=weekly&date=2026-06-01").get_data(as_text=True)
    band = re.search(r'<tr class="groupband">(.*?)</tr>', html, re.S)
    weeks = re.findall(r">Week (\d+)</th>", band.group(1))
    assert len(weeks) > 1, "weekly should put several weeks across the page"
    assert [int(w) for w in weeks] == sorted(int(w) for w in weeks)


def test_a_repeated_band_is_merged_not_repeated(client):
    """Twelve months in one year is one '2026' heading, not twelve of them."""
    html = client.get("/?report=yearly&date=2026-06-01").get_data(as_text=True)
    band = re.search(r'<tr class="groupband">(.*?)</tr>', html, re.S)
    if band:
        years = re.findall(r">(\d{4})</th>", band.group(1))
        assert len(years) <= 1, f"year heading repeated {len(years)} times"


# --- five is a floor, not a cap --------------------------------------------

def test_a_quiet_day_explains_why_it_shows_five(client):
    """Five groups on a quiet day is indistinguishable from the old cap.

    Without saying so, the tool looks exactly like the defect it exists to
    fix, and the reader concludes it is broken.
    """
    html = client.get("/?report=daily&date=2026-06-02").get_data(as_text=True)
    if "No punches found" in html:
        pytest.skip("fixture has no data on this day")
    assert "minimum layout, not a limit" in html
    assert "/busiest?" in html


def test_a_deep_day_does_not_show_that_explanation(client):
    html = client.get("/?report=daily&date=2026-06-01").get_data(as_text=True)
    assert "minimum layout, not a limit" not in html


def test_busiest_redirects_to_the_deepest_day_of_the_month(client):
    """The fixture's heavy day is 2026-06-01 with eleven breaks."""
    response = client.get("/busiest?report=daily&date=2026-06-20")
    assert response.status_code == 302
    assert "date=2026-06-01" in response.headers["Location"]


def test_busiest_keeps_the_employee_filter(client):
    response = client.get("/busiest?report=daily&date=2026-06-20&employee=1")
    assert response.status_code == 302
    assert "employee=1" in response.headers["Location"]
