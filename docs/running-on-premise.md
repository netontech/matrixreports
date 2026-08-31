# Running it on the Matrix server's network

The Matrix database lives on your network, so the reports have to be generated
from a machine that can reach it. This walks through that setup on Windows,
which is where COSEC installations normally sit.

Nothing here sends data anywhere. The tool connects to SQL Server, reads, and
writes .xlsx files to a local folder.

## 1. Prerequisites

On a machine that can reach the SQL Server instance:

* **Python 3.10 or later** — <https://www.python.org/downloads/windows/>.
  Tick **"Add python.exe to PATH"** in the installer.
* **Microsoft ODBC Driver 18 for SQL Server** —
  <https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>.
  Check what you already have:

  ```powershell
  Get-OdbcDriver | Where-Object Name -like "*SQL Server*" | Select-Object Name
  ```

  Put whatever that prints into `odbc_driver` in the config.

## 2. Install

```powershell
git clone https://github.com/netontech/matrixreports.git
cd matrixreports
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[sqlserver]"
```

Check it is on the path:

```powershell
matrixreports --help
```

## 3. A read-only login

The tool only ever issues `SELECT`. Give it a login that can do no more — this
protects the attendance data from the reporting tool, whatever else happens.

```sql
CREATE LOGIN matrixreports WITH PASSWORD = 'choose-a-strong-one';
USE COSEC;                       -- your Matrix database name
CREATE USER matrixreports FOR LOGIN matrixreports;
ALTER ROLE db_datareader ADD MEMBER matrixreports;
```

Windows authentication works too — see the `dsn` example below, and skip the
login above.

## 4. Point it at the database

```powershell
copy config\matrixreports.example.yaml config\matrixreports.yaml
notepad config\matrixreports.yaml
```

Only the `database` block matters at this stage; discovery fills in the rest.

```yaml
database:
  driver: sqlserver
  host: SERVERNAME\SQLEXPRESS      # or an IP
  port: 1433
  database: COSEC
  user: matrixreports
  password: "..."
  odbc_driver: "ODBC Driver 18 for SQL Server"
```

For Windows authentication, use a full connection string instead:

```yaml
database:
  driver: sqlserver
  dsn: "DRIVER={ODBC Driver 18 for SQL Server};SERVER=SERVERNAME\\SQLEXPRESS;DATABASE=COSEC;Trusted_Connection=yes;TrustServerCertificate=yes"
```

`config/matrixreports.yaml` is in `.gitignore`, so a password put there is not
committed. Better still, leave `password` empty and use a trusted connection.

## 5. Let it find the schema

You do not have to map the tables by hand:

```powershell
matrixreports discover --write config\discovered.yaml
```

It reads the database catalogue, scores every table for how well it fits each
role, and writes a draft config. Expect something like:

```
Tables inspected: 214

Employee master    mx_employee_master  (312 rows, score 125)
                   - id 'EmployeeID', name 'EmployeeName'
Punch log          mx_attendance_log  (1,284,551 rows, score 155)
                   - employee column matches the employee master
                   - stamp column 'PunchDateTime'
                   - direction column 'InOutFlag'
Leave register     mx_leave_register  (1,204 rows, score 60)

Pre-flattened summary tables (a fixed in/out limit lives here):
  mx_daily_attendance: 12 IN/OUT slot columns

Direction values sampled: '1', '2'
```

That last section is the thing to look at. A table with `IN1, OUT1, IN2, OUT2 …`
columns can only hold as many punches as it has column pairs — **that is where
the six in/out limit comes from**. The stock report reads it; this tool reads the
raw log instead and never selects a flattened table as a source.

**Review the draft before using it.** Two things especially:

* `direction_in` / `direction_out` — getting these backwards inverts every
  session, turning time at work into time out. The comment above them lists the
  actual values found in the column.
* The chosen punch table. If discovery picked something with roughly
  `employees × days` rows rather than `employees × punches × days`, it found a
  summary table. Pick a different one from the listed alternatives.

## 6. Confirm it before trusting a report

```powershell
matrixreports --config config\discovered.yaml check --from 2026-06-01 --to 2026-06-30
```

This is also the evidence to show the client:

```
Deepest day:      23 punches, 10 breaks
Report would use: 10 OUT/IN groups (the stock Matrix report is fixed at 5 breaks + 1 final OUT)

Breaks per day    count
    5                25
    6                26   <-- dropped by the stock report
    8                35   <-- dropped by the stock report

102 day record(s) carry more than 5 breaks. Those punches exist in the
database and are reported in full here.
```

Sanity-check a handful of rows against what the Matrix report shows for the same
day. `1st In` and `Last Out` should agree; the break columns are where this tool
shows more.

## 7. Generate the reports

```powershell
matrixreports --config config\discovered.yaml daily   --date 2026-06-01
matrixreports --config config\discovered.yaml summary --date 2026-06-01
matrixreports --config config\discovered.yaml weekly  --from 2026-06-01 --to 2026-06-30
matrixreports --config config\discovered.yaml monthly --month 2026-06
matrixreports --config config\discovered.yaml yearly  --year 2026
```

Workbooks land in `out\` unless you pass `--out`.

## 8. Set the shift rules

Discovery cannot infer these; they come from HR policy. The defaults are read off
the reports you already circulate:

```yaml
shift:
  start: "10:00"
  end: "19:00"
  late_in_grace: "00:10"        # late after 10:10
  late_out_grace: "00:10"       # late out after 19:10
  weekly_off_days: [4]          # 0=Mon .. 6=Sun, so 4 is Friday
```

If departments run different shifts, this needs extending — the tool currently
applies one shift company-wide. Worth confirming before anyone acts on the
`Late IN` and `Early OUT` columns.

Set `pairing.day_start_hour: 4` if any shift runs past midnight, so a night shift
stays on one row instead of being split across two days.

## Running it on a schedule

Task Scheduler, daily at 07:00:

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\matrixreports\.venv\Scripts\matrixreports.exe" `
    -Argument "--config C:\matrixreports\config\discovered.yaml daily --date TODAY" `
    -WorkingDirectory "C:\matrixreports"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00am
Register-ScheduledTask -TaskName "Attendance daily report" -Action $action -Trigger $trigger
```

`--date` needs a real date, so in practice wrap it in a one-line script:

```powershell
$yesterday = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
matrixreports --config config\discovered.yaml daily --date $yesterday
matrixreports --config config\discovered.yaml summary --date $yesterday
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `Data source name not found` | The `odbc_driver` string does not match an installed driver. Run the `Get-OdbcDriver` command above. |
| `SSL Provider: certificate chain ... not trusted` | Driver 18 verifies TLS by default. Add `TrustServerCertificate=yes` to the `dsn`, or install the server certificate. |
| `Login failed for user` | Check the login exists on *that* instance and has been granted `db_datareader` on the right database. |
| `could not connect: ... timeout` | TCP/IP is often disabled on SQL Express. Enable it in SQL Server Configuration Manager and check port 1433 through the firewall. |
| `No punches found` | Wrong table, or a `where` clause filtering everything. Re-run `discover` and check the alternatives. |
| Everything shows 0 breaks | You are pointed at a flattened summary table, not the raw log. |
| Many `DIRECTION_INFERRED` flags | `direction_in` / `direction_out` do not match the stored values. |
| Hours look inverted, breaks huge | `direction_in` and `direction_out` are the wrong way round. |

## If the machine has no internet access

`pip install` needs to reach PyPI. On an isolated server, download the wheels
elsewhere and copy them over:

```powershell
# on a machine with internet, matching the server's Python version:
pip download openpyxl PyYAML pyodbc -d wheels
# copy the wheels folder across, then on the server:
pip install --no-index --find-links wheels openpyxl PyYAML pyodbc
pip install -e . --no-deps
```
