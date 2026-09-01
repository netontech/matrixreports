# Mapping the Matrix database

This package needs four things from the Matrix database. Only the first two are
required.

| What | Required | Used for |
| --- | --- | --- |
| Employee master | yes | names, codes, departments |
| Raw punch / attendance log | yes | everything else |
| Leave register | no | `Sick Leave` / `Annual Leave` statuses |
| Holiday master | no | `HOL` days and the attendance percentage |

Matrix COSEC table names differ between versions and installations, so the names
live in `config/matrixreports.yaml` rather than in code. The example config
ships with placeholder names in the shape COSEC commonly uses — treat them as a
starting point to verify, not as correct.

## Start with `discover`

Before mapping anything by hand, try:

```bash
matrixreports discover --write config/discovered.yaml
```

It inspects the database catalogue and writes a draft mapping, including a
warning for any pre-flattened summary table it finds.

Working from a dump file instead of a live connection? Point it at the `.sql`
files and it parses the `CREATE TABLE` statements — no database required:

```bash
matrixreports discover --sql-file Cosec.sql --sql-file Cosec_data.sql \
    --write config/discovered.yaml
```

T-SQL (`[dbo].[table]`) and MySQL (`` `table` ``) syntax are both understood, as
are UTF-16 files, which is what SSMS writes by default. If the dump also carries
`INSERT` statements, the direction column's values are read from them and the
statement counts stand in for row counts — which is what distinguishes the raw
punch log from a daily summary.

The rest of this document covers doing it manually, and what to check in the
draft. For a mapping already confirmed against a real COSEC database — including
the two columns `discover` reliably gets wrong — see
[`cosec-schema-verified.md`](cosec-schema-verified.md).

## Finding the right tables in the SQL dump

If you have the `.sql` dump or a connection to the server, these queries find the
tables quickly.

```sql
-- SQL Server: every table, largest first. The punch log is normally by far the
-- biggest table in the database.
SELECT  t.name AS table_name, SUM(p.rows) AS row_count
FROM    sys.tables t
JOIN    sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY t.name
ORDER BY row_count DESC;

-- Candidate punch tables: any table with a datetime column and an employee column.
SELECT  c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE
FROM    INFORMATION_SCHEMA.COLUMNS c
WHERE   c.DATA_TYPE IN ('datetime', 'datetime2', 'smalldatetime', 'date', 'time')
   OR   c.COLUMN_NAME LIKE '%EMP%'
ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION;
```

If you only have the dump file, the same information is in the `CREATE TABLE`
statements:

```bash
grep -n "CREATE TABLE" dump.sql | head -50
grep -iE "create table.*(att|punch|log|event|swipe|trans)" dump.sql
```

You are looking for the table with **one row per badge read** — not a daily
summary table. A punch table has roughly (employees x punches per day x days)
rows; a summary table has (employees x days). Any table with columns named like
`IN1, OUT1, IN2, OUT2, ...` is a pre-flattened summary and is where the
six-in/out limit comes from — do not point this tool at it.

## Filling in the config

```yaml
schema:
  employees:
    table: EmployeeMaster
    columns:
      id: EmployeeID          # must join to the punch table's employee column
      code: EmployeeCode
      name: EmployeeName
      department: Department
    where: "IsActive = 1"     # optional

  punches:
    table: AttendanceLog
    columns:
      emp_id: EmployeeID
      timestamp: PunchDateTime
      direction: InOutFlag    # optional - omit and direction is inferred
      device: DoorName        # optional
```

### If the stamp is split across two columns

Some installations store `PunchDate` (date) and `PunchTime` (time) separately:

```yaml
schema:
  punch_datetime_is_split: true
  punches:
    table: AttendanceLog
    columns:
      emp_id: EmployeeID
      date: PunchDate
      time: PunchTime
```

### If direction is encoded differently

Values are compared as text, case-insensitively:

```yaml
schema:
  direction_in:  ["1", "I", "IN", "ENTRY"]
  direction_out: ["0", "O", "OUT", "EXIT"]
```

If the table records no direction at all, leave `direction` unmapped and set:

```yaml
pairing:
  direction_mode: alternate
```

Punches are then paired by strict alternation, first read of the day being an
entry. Days where that assumption breaks are flagged `DIRECTION_INFERRED` and
`MISSING_IN`/`MISSING_OUT` in the report's *Data Issues* column.

### If the layout needs a join

Anything the generator cannot express goes in `queries` as full SQL. Each query
receives exactly two positional parameters, the period start and end:

```yaml
queries:
  punches: |
    SELECT e.EmployeeID, l.PunchDateTime, l.InOutFlag, d.DoorName
    FROM AttendanceLog l
    JOIN EmployeeMaster e ON e.EmployeeID = l.EmployeeID
    LEFT JOIN DoorMaster d ON d.DoorID = l.DoorID
    WHERE l.PunchDateTime BETWEEN ? AND ?
    ORDER BY e.EmployeeID, l.PunchDateTime
```

Column order matters: `emp_id, timestamp, direction, device`.

## Verifying the mapping

```bash
matrixreports check --from 2026-06-01 --to 2026-06-30
```

`check` prints the employee count, the deepest day found, and a histogram of
breaks per day, marking the rows the stock report drops. Read it as:

* **`No punches found`** — the punch table or its `where` clause is wrong.
* **Employees found but no punches** — `employees.id` and `punches.emp_id` are
  probably different keys (one an internal ID, the other a badge code).
* **Every day shows 0 breaks** — you are pointed at a daily summary table, not
  the raw log.
* **Many `DIRECTION_INFERRED` flags** — the `direction_in` / `direction_out`
  values do not match what the column actually stores.

## Access

The tool only ever issues `SELECT`. A read-only login is sufficient and is what
should be used.

## No database access yet?

Export the punch table to CSV and pass `--csv`:

```sql
SELECT EmployeeID AS emp_id, EmployeeName AS name, Department AS department,
       PunchDateTime AS timestamp, InOutFlag AS direction
FROM   AttendanceLog l JOIN EmployeeMaster e ON e.EmployeeID = l.EmployeeID
WHERE  PunchDateTime >= '2026-06-01'
```

```bash
matrixreports --csv punches.csv daily --date 2026-06-01
```

Recognised headers (case-insensitive): `emp_id`/`employee_id`/`code`, `name`,
`department`, `timestamp` (or `date` + `time`), `direction`, `device`.
