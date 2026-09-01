# matrixreports

Attendance reports built from the raw punch data in a Matrix (COSEC) database,
with **no limit on the number of in/out punches per day**.

## The problem

The Matrix reporting module lays each attendance day out as six fixed
`IN`/`OUT` groups:

```
1st In | Last Out | [OUT IN MINS] x5 | OUT | No. Of OUT | Total Out Minutes | ...
```

Five of those groups carry a break, so the report can express at most five
step-outs. Anyone who leaves the floor a sixth time has the rest of their
movement dropped before the sheet is written — and because the totals are
derived from what the sheet can show, their **hours worked are overstated by the
length of every break that did not fit**.

The punches themselves are all in the database. This is a limitation of the
report writer, not of the data, which is why the database looks complete when it
is checked directly.

## The fix

Read the raw punch rows and pair them in code, where nothing is fixed at six:

```
punches -> sessions -> breaks -> report columns sized to the data
```

A day with 24 punches produces 12 sessions and 11 breaks, and the daily report
grows to 11 `OUT`/`IN`/`MINS` groups. A quiet day still renders at the familiar
five so the sheet looks like the one people already read.

See [`docs/findings.md`](docs/findings.md) for the full analysis of the current
reports, including a second, independent defect: durations are stored as `hh.mm`
decimals (`8.14` for 8h14m) and then `SUM`med, so every weekly and monthly total
is arithmetically wrong.

## Install

```bash
pip install -e .                      # add [sqlserver], [mysql] or [postgres]
```

Run it on a machine that can reach the Matrix SQL Server.
[`docs/running-on-premise.md`](docs/running-on-premise.md) covers the Windows
setup end to end: ODBC driver, a read-only login, scheduling and troubleshooting.

## Configure

```bash
cp config/matrixreports.example.yaml config/matrixreports.yaml
```

Fill in the `database` block, then let the tool work out the schema for itself:

```bash
matrixreports discover --write config/discovered.yaml
```

If you have a `.sql` dump but no database to connect to, discovery reads the
dump directly — no connection, no `database` block needed:

```bash
matrixreports discover --sql-file Cosec.sql --write config/discovered.yaml
```

`discover` reads the database catalogue, scores every table for how well it fits
each role, samples the direction column so `IN` and `OUT` can be mapped, and
writes a draft config annotated with what it found and why. It also reports any
**pre-flattened summary table** — one with `IN1, OUT1, IN2, OUT2 …` columns,
which can hold only as many punches as it has column pairs. That is where a fixed
in/out ceiling comes from, and such tables are never selected as a source.

Review the draft before using it — particularly `direction_in` / `direction_out`,
since reversing those turns time at work into time out.
[`docs/schema-mapping.md`](docs/schema-mapping.md) covers mapping by hand if
discovery cannot work something out.

A mapping already verified against a real COSEC database, with the evidence
behind it, is in [`docs/cosec-schema-verified.md`](docs/cosec-schema-verified.md)
and `config/matrix-cosec-verified.example.yaml`. Start there if the client runs
stock Matrix table names — but still run `check` before trusting it.

## Use

Then `check`, which confirms the mapping and proves the point on real data:

```console
$ matrixreports check --from 2026-06-01 --to 2026-06-30
Employees:        10
Days in range:    30
Deepest day:      24 punches, 11 breaks
Report would use: 11 OUT/IN groups (the stock Matrix report is fixed at 5 breaks + 1 final OUT)

Breaks per day    count
    5                25
    6                26   <-- dropped by the stock report
    8                35   <-- dropped by the stock report
   11                 3   <-- dropped by the stock report

102 day record(s) carry more than 5 breaks. Those punches exist in the
database and are reported in full here.
```

Then generate reports:

```bash
matrixreports daily   --date 2026-06-01
matrixreports summary --date 2026-06-01          # daily exception summary
matrixreports weekly  --from 2026-06-01 --to 2026-06-30
matrixreports monthly --month 2026-06
matrixreports yearly  --year 2026
```

Useful flags: `--out PATH`, `--format csv`, `--employee CODE` (repeatable),
`--groups N` to pin the layout to a fixed width, and `--csv extract.csv` to run
from an exported CSV instead of a live connection.

## Try it without a database

```bash
python examples/generate_demo.py --out out/demo
```

Builds a synthetic Matrix-shaped SQLite database — including staff who step out
eight, ten and thirteen times a day — and writes all four reports.

## Reports

| Command | Shape |
| --- | --- |
| `daily` | One row per employee. `1st In`, `Last Out`, an `OUT`/`IN`/`MINS` group per break, then `No. Of OUT`, `Total Out Time`, `Actual Works Hours`, `Wrk Hrs + Out Time`, `Late IN`, `Early OUT`, `Late OUT`, `Remarks`, and a `Data Issues` column. |
| `summary` | The exception blocks — early in, late in, late out, early out, working during off, long breaks, missed punches, not present, headcount — derived from the same day records as `daily`, stacked as filterable tables. |
| `weekly` | Employees down, weeks across, exact hour totals. |
| `monthly` | Employees down, days across; hours worked, or a status code (`OFF`, `HOL`, `Sick Leave`, `A`). |
| `yearly` | Employees down, months across. |

Durations are written as Excel time values with a `[h]:mm` format, so they
display as `HH:MM`, sum correctly in the sheet, and do not wrap at 24 hours.

## Handling imperfect punch data

Real doors produce messy reads. The behaviour is configurable under `pairing:`
and every adjustment is recorded in the report's *Data Issues* column rather
than applied silently.

| Situation | Default |
| --- | --- |
| Same badge read twice within 60s | Collapsed (`DUPLICATE_PUNCH`) |
| Two `IN`s in a row | Earliest kept (`CONSECUTIVE_IN`) |
| Two `OUT`s in a row | Latest kept (`CONSECUTIVE_OUT`) |
| No final `OUT` | Closed at shift end (`MISSING_OUT`) |
| `OUT` with no matching `IN` | Not counted (`MISSING_IN`) |
| No direction recorded | Inferred by alternation (`DIRECTION_INFERRED`) |
| Shift crossing midnight | Set `pairing.day_start_hour: 4` |

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Layout

```
src/matrixreports/
  duration.py     exact HH:MM arithmetic; never hh.mm decimals
  model.py        Employee, Punch, Interval, DayRecord
  pairing.py      punches -> unlimited sessions and breaks
  datasource.py   SQL / CSV / in-memory sources
  discover.py     works out the schema by inspecting the catalogue
  builder.py      day records for a whole period
  reports/        daily, summary, weekly, monthly, yearly
  excel.py        .xlsx and .csv output
  cli.py          command line entry point
```
