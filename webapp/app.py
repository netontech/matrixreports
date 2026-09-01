"""A small web front end for matrixreports.

Point it at the same config the CLI uses and it serves the reports in a browser,
laid out the way the client's sheet is laid out — but with as many OUT/IN/MINS
groups as the day actually needs, instead of a fixed five.

    pip install -e ".[web]"
    matrixreports-web --config config/matrixreports.yaml

It only ever issues SELECT, exactly like the CLI: it holds no state, writes
nothing, and every request re-reads the database.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from datetime import date, datetime, timedelta

from flask import Flask, Response, render_template, request, send_file

from matrixreports.builder import AttendanceBuilder
from matrixreports.config import Config
from matrixreports.datasource import open_source
from matrixreports.excel import write_csv, write_workbook
from matrixreports.reports import (
    build_daily_report,
    build_monthly_report,
    build_summary_report,
    build_weekly_report,
    build_yearly_report,
)

from .auth import (InsecureBindError, guard_bind, hash_password,
                   require_login)
from .render import band, cell_class, cell_text, render_rows

app = Flask(__name__)
app.config["MATRIX_CONFIG"] = os.environ.get("MATRIXREPORTS_CONFIG", "config/matrixreports.yaml")
app.before_request(require_login)

REPORTS = {
    "daily": "Daily attendance",
    "summary": "Daily exception summary",
    "weekly": "Weekly totals",
    "monthly": "Monthly attendance",
    "yearly": "Yearly attendance",
}


def _config() -> Config:
    return Config.load(app.config["MATRIX_CONFIG"])


def _range(kind: str, day: date) -> tuple[date, date]:
    if kind in {"daily", "summary"}:
        return day, day
    if kind == "weekly":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    if kind == "monthly":
        start = day.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt - timedelta(days=1)
    return day.replace(month=1, day=1), day.replace(month=12, day=31)


def _build(kind: str, day: date, groups: int | None, employees: list[str]):
    """Run the real pipeline. Returns (tables, book, config)."""
    config = _config()
    if groups:
        # 0 means "size to the data"; setting both pins the layout to a width.
        config.report.max_inout_groups = groups
        config.report.min_inout_groups = groups
    start, end = _range(kind, day)
    with open_source(config) as source:
        book = AttendanceBuilder(config, source).build(
            start, end, employee_ids=employees or None
        )
    if kind == "daily":
        tables = [build_daily_report(book, start)]
    elif kind == "summary":
        tables = build_summary_report(book, start)
    elif kind == "weekly":
        tables = [build_weekly_report(book)]
    elif kind == "monthly":
        tables = [build_monthly_report(book)]
    else:
        tables = [build_yearly_report(book)]
    return tables, book, config


def _form_args():
    kind = request.args.get("report", "daily")
    if kind not in REPORTS:
        kind = "daily"
    raw = request.args.get("date", "")
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        day = date.today()
    try:
        groups = int(request.args.get("groups") or 0) or None
    except ValueError:
        groups = None
    employees = [e.strip() for e in request.args.get("employee", "").split(",") if e.strip()]
    return kind, day, groups, employees


def _step(kind: str, day: date, delta: int) -> date:
    """Previous/next in the units the chosen report actually moves in."""
    if kind in {"daily", "summary"}:
        return day + timedelta(days=delta)
    if kind == "weekly":
        return day + timedelta(weeks=delta)
    if kind == "monthly":
        first = day.replace(day=1)
        return (first + timedelta(days=32 * delta)).replace(day=1) if delta > 0 \
            else (first - timedelta(days=1)).replace(day=1)
    return day.replace(year=day.year + delta)


def _shell(kind, day, groups, employees, **extra):
    """Everything the chrome needs, whichever template renders."""
    return dict(
        reports=REPORTS, kind=kind, day=day,
        groups=groups or "", employees=",".join(employees),
        prev_day=_step(kind, day, -1), next_day=_step(kind, day, 1),
        today=date.today(), **extra,
    )


@app.route("/")
def index():
    kind, day, groups, employees = _form_args()
    if "date" not in request.args:
        return render_template("index.html", **_shell(kind, day, groups, employees))
    try:
        tables, book, config = _build(kind, day, groups, employees)
    except Exception as exc:                          # noqa: BLE001 - shown to the user
        return render_template("index.html",
                               **_shell(kind, day, groups, employees, error=str(exc))), 500
    return render_template(
        "report.html",
        **_shell(kind, day, groups, employees,
                 tables=tables, band=band, cell_text=cell_text, cell_class=cell_class,
                 render_rows=render_rows,
                 max_breaks=book.max_breaks, max_punches=book.max_punches,
                 shift=config.shift, query=request.query_string.decode()),
    )


@app.route("/download.<fmt>")
def download(fmt: str):
    if fmt not in {"xlsx", "csv"}:
        return Response("unknown format", status=404)
    kind, day, groups, employees = _form_args()
    tables, _book, _config = _build(kind, day, groups, employees)
    stem = f"{kind}_{day:%Y-%m-%d}"
    # The exporters write to a path, so stage the file and stream it back.
    tmp = Path(tempfile.mkdtemp()) / f"{stem}.{fmt}"
    if fmt == "xlsx":
        write_workbook(tables, tmp)
        mimetype = ("application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet")
    else:
        write_csv(tables[0], tmp)
        mimetype = "text/csv"
    return send_file(tmp, as_attachment=True, download_name=tmp.name,
                     mimetype=mimetype)


def main() -> int:
    parser = argparse.ArgumentParser(description="Web front end for matrixreports")
    parser.add_argument("--config", default=app.config["MATRIX_CONFIG"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--hash-password", action="store_true",
                        help="prompt for a password and print its hash, for "
                             "MATRIXREPORTS_AUTH_PASSWORD_HASH")
    args = parser.parse_args()

    if args.hash_password:
        import getpass
        first = getpass.getpass("password: ")
        if first != getpass.getpass("again: "):
            print("passwords did not match", file=sys.stderr)
            return 1
        if len(first) < 12:
            print("use at least 12 characters", file=sys.stderr)
            return 1
        print(hash_password(first))
        return 0

    try:
        guard_bind(args.host)
    except InsecureBindError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    app.config["MATRIX_CONFIG"] = args.config
    print(f"matrixreports web on http://{args.host}:{args.port}  (config: {args.config})")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
