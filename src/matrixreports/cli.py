"""Command line interface.

    matrixreports discover --config config.yaml   # writes a draft schema mapping
    matrixreports check    --config config.yaml
    matrixreports daily    --date 2026-06-01
    matrixreports summary  --date 2026-06-01
    matrixreports weekly   --from 2026-06-01 --to 2026-06-30
    matrixreports monthly  --month 2026-06
    matrixreports yearly   --year 2026

``check`` is the one to run first against a new installation: it reports how
many punches a day actually carries, which is the evidence that the data the
stock report drops is present in the database all along.
"""

from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date, datetime
from pathlib import Path

from .builder import AttendanceBuilder
from .config import Config
from .datasource import SqlPunchSource, open_source
from .discover import discover, format_report, render_config
from .duration import hhmm
from .excel import write_csv, write_csvs, write_workbook
from .reports import (
    build_daily_report,
    build_monthly_report,
    build_summary_report,
    build_weekly_report,
    build_yearly_report,
    group_count,
)


def _parse_date(text: str) -> date:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"not a date: {text!r} (use YYYY-MM-DD)")


def _parse_month(text: str) -> tuple[date, date]:
    parsed = datetime.strptime(text, "%Y-%m").date()
    last = calendar.monthrange(parsed.year, parsed.month)[1]
    return parsed, parsed.replace(day=last)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matrixreports",
        description="Attendance reports from the Matrix database, with no limit "
                    "on the number of in/out punches per day.",
    )
    parser.add_argument("--config", default="config/matrixreports.yaml",
                        help="path to the YAML config (default: %(default)s)")
    parser.add_argument("--csv", dest="csv_extract",
                        help="read punches from a CSV extract instead of the database")
    parser.add_argument("--out", help="output file (or directory when --format csv)")
    parser.add_argument("--format", choices=["xlsx", "csv"], default="xlsx")
    parser.add_argument("--employee", action="append", dest="employees",
                        help="restrict to an employee id/code (repeatable)")
    parser.add_argument("--groups", type=int,
                        help="force a fixed number of OUT/IN groups (default: fit the data)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_parser = subparsers.add_parser(
        "discover",
        help="inspect the database and write a draft schema mapping",
    )
    discover_parser.add_argument(
        "--write", metavar="PATH",
        help="write the draft config to PATH (default: print to stdout)",
    )
    discover_parser.add_argument(
        "--no-sample", action="store_true",
        help="do not read sample values from the direction column",
    )

    check = subparsers.add_parser("check", help="verify connectivity and show punch depth")
    check.add_argument("--from", dest="start", type=_parse_date, required=True)
    check.add_argument("--to", dest="end", type=_parse_date)

    daily = subparsers.add_parser("daily", help="daily attendance report")
    daily.add_argument("--date", type=_parse_date, required=True)

    summary = subparsers.add_parser("summary", help="daily exception summary")
    summary.add_argument("--date", type=_parse_date, required=True)

    weekly = subparsers.add_parser("weekly", help="weekly totals per employee")
    weekly.add_argument("--from", dest="start", type=_parse_date, required=True)
    weekly.add_argument("--to", dest="end", type=_parse_date, required=True)
    weekly.add_argument("--week-start", type=int, default=0,
                        help="weekday the week starts on, 0=Monday (default: %(default)s)")

    monthly = subparsers.add_parser("monthly", help="day-by-day grid for a month")
    monthly.add_argument("--month", required=True, help="YYYY-MM")

    yearly = subparsers.add_parser("yearly", help="month-by-month grid for a year")
    yearly.add_argument("--year", type=int, required=True)

    return parser


def _load_config(path: str) -> Config:
    config_path = Path(path)
    if not config_path.exists():
        print(
            f"config not found: {config_path}\n"
            "Copy config/matrixreports.example.yaml and fill in the database "
            "and schema sections (see docs/schema-mapping.md).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return Config.load(config_path)


def _run_discover(config: Config, args: argparse.Namespace) -> int:
    """Inspect the catalogue and emit a draft mapping.

    Runs before any schema mapping exists, so it talks to the connection
    directly rather than going through the punch source.
    """
    if args.csv_extract:
        print("discover needs a database connection; it cannot inspect a CSV.",
              file=sys.stderr)
        return 2
    try:
        source = SqlPunchSource.__new__(SqlPunchSource)
        source.config = config
        source.driver = (config.database.driver or "sqlite").lower()
        connection = source._connect(config.database)
    except Exception as exc:                     # noqa: BLE001 - surfaced to the user
        print(f"could not connect: {exc}\n\n"
              "Check the `database` section of your config; see "
              "docs/running-on-premise.md for connection strings.",
              file=sys.stderr)
        return 2

    try:
        result = discover(connection, source.driver,
                          sample_directions=not args.no_sample)
    finally:
        try:
            connection.close()
        except Exception:
            pass

    print(format_report(result), file=sys.stderr)

    if not result.best("punches") or not result.best("employees"):
        print("\nCould not identify both an employee master and a punch log. "
              "Map them by hand using docs/schema-mapping.md.", file=sys.stderr)
        return 1

    database = {
        "driver": config.database.driver,
        "host": config.database.host,
        "port": config.database.port,
        "database": config.database.database,
        "user": config.database.user,
        "odbc_driver": config.database.odbc_driver,
        "dsn": config.database.dsn,
        "path": config.database.path,
    }
    text = render_config(result, database=database)

    if args.write:
        path = Path(args.write)
        if path.exists():
            print(f"\nrefusing to overwrite {path}; choose another path or move it "
                  "aside", file=sys.stderr)
            return 2
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"\nwrote draft config to {path}\n"
              f"Review it, then run: matrixreports --config {path} check "
              "--from <date> --to <date>", file=sys.stderr)
    else:
        print(text)
    return 0


def _range_for(args: argparse.Namespace) -> tuple[date, date]:
    if args.command in {"daily", "summary"}:
        return args.date, args.date
    if args.command == "weekly":
        return args.start, args.end
    if args.command == "monthly":
        return _parse_month(args.month)
    if args.command == "yearly":
        return date(args.year, 1, 1), date(args.year, 12, 31)
    if args.command == "check":
        return args.start, args.end or args.start
    raise ValueError(f"unhandled command {args.command!r}")


def _default_out(args: argparse.Namespace, start: date, end: date) -> Path:
    stem = {
        "daily": f"daily_attendance_{start:%Y-%m-%d}",
        "summary": f"attendance_summary_{start:%Y-%m-%d}",
        "weekly": f"weekly_attendance_{start:%Y-%m-%d}_{end:%Y-%m-%d}",
        "monthly": f"monthly_attendance_{start:%Y-%m}",
        "yearly": f"yearly_attendance_{start:%Y}",
    }[args.command]
    return Path("out") / (stem + (".xlsx" if args.format == "xlsx" else ""))


def _run_check(book, config) -> int:
    print(f"Employees:        {len(book.employees)}")
    print(f"Days in range:    {len(book.days)}")
    with_data = [record for record in book.records.values() if record.has_data]
    print(f"Day records with punches: {len(with_data)}")
    if not with_data:
        print("\nNo punches found. Check schema.punches in the config "
              "(see docs/schema-mapping.md).")
        return 1

    print(f"Deepest day:      {book.max_punches} punches, {book.max_breaks} breaks")
    print(f"Report would use: {group_count(book, config)} OUT/IN groups "
          "(the stock Matrix report is fixed at 5 breaks + 1 final OUT)\n")

    histogram: dict[int, int] = {}
    for record in with_data:
        histogram[record.break_count] = histogram.get(record.break_count, 0) + 1
    print("Breaks per day    count")
    over = 0
    for breaks in sorted(histogram):
        marker = "   <-- dropped by the stock report" if breaks > 5 else ""
        print(f"  {breaks:>3}             {histogram[breaks]:>5}{marker}")
        if breaks > 5:
            over += histogram[breaks]
    if over:
        print(f"\n{over} day record(s) carry more than 5 breaks. "
              "Those punches exist in the database and are reported in full here.")
    else:
        print("\nNo day in this range exceeds 5 breaks; widen the range to see the effect.")

    flagged = [record for record in with_data if record.anomalies]
    if flagged:
        counts: dict[str, int] = {}
        for record in flagged:
            for anomaly in record.anomalies:
                counts[anomaly.value] = counts.get(anomaly.value, 0) + 1
        print("\nData quality flags:")
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {name:<22} {count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = _load_config(args.config)
    if args.groups:
        config.report.max_inout_groups = args.groups
        config.report.min_inout_groups = args.groups

    if args.command == "discover":
        return _run_discover(config, args)

    start, end = _range_for(args)

    try:
        with open_source(config, csv_path=args.csv_extract) as source:
            book = AttendanceBuilder(config, source).build(
                start, end, employee_ids=args.employees
            )
    except KeyError as exc:
        print(f"schema mapping incomplete: {exc.args[0]}", file=sys.stderr)
        return 2
    except Exception as exc:                     # noqa: BLE001 - surfaced to the user
        print(
            f"could not read attendance data: {exc}\n\n"
            "Check the database and schema sections of "
            f"{config.source_path or args.config}. The table and column names "
            "must match the Matrix database exactly; see docs/schema-mapping.md "
            "for how to find them.",
            file=sys.stderr,
        )
        return 2

    if args.command == "check":
        return _run_check(book, config)

    if args.command == "daily":
        tables = [build_daily_report(book, start)]
        combine = False
    elif args.command == "summary":
        tables = build_summary_report(book, start)
        combine = True
    elif args.command == "weekly":
        tables = [build_weekly_report(book, week_start=args.week_start)]
        combine = False
    elif args.command == "monthly":
        tables = [build_monthly_report(book)]
        combine = False
    else:
        tables = [build_yearly_report(book)]
        combine = False

    out = Path(args.out) if args.out else _default_out(args, start, end)
    if args.format == "csv":
        if len(tables) == 1:
            written = [write_csv(tables[0], out if out.suffix else out / f"{tables[0].key}.csv")]
        else:
            written = write_csvs(tables, out)
        for path in written:
            print(f"wrote {path}")
    else:
        path = write_workbook(tables, out, combine=combine)
        print(f"wrote {path}")

    rows = sum(len(table.rows) for table in tables)
    print(f"{rows} row(s); deepest day in range: {book.max_punches} punches, "
          f"{book.max_breaks} breaks")
    total = sum(
        (record.worked for record in book.records.values() if record.worked),
        __import__("datetime").timedelta(0),
    )
    print(f"total hours in range: {hhmm(total)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
