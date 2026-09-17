# Runbook — installing on the client's Windows Server

Done remotely over AnyDesk. About 60 minutes once you are connected.

Follow it in order. Every step has a check — **if a check fails, stop there**
rather than carrying on, because a later step will fail more confusingly and
you will end up debugging the wrong thing.

---

## What you need before you connect

**Three things from their side. Get all three before starting.**

| # | What | Looks like | Why |
| --- | --- | --- | --- |
| 1 | **Matrix SQL Server address** | `10.20.30.40` or `SQLHOST\COSEC` | What we connect to |
| 2 | **Authentication method** | Windows, or SQL login | Changes the config line entirely |
| 3 | **The credentials** | A service account, or a username and password | How we sign in |

On (2), ask their DBA which they prefer — **Windows Authentication is better
for everyone**: nothing about it gets written into our config file, so there is
no password sitting on their server for someone to find later. If they say SQL
login, that is fine too; step 5 covers both.

Whichever it is, we need **read access only**. If the account does not exist
yet, send them this:

```sql
-- Windows Authentication
CREATE LOGIN [DOMAIN\svc_matrixreports] FROM WINDOWS;
USE COSEC;
CREATE USER [DOMAIN\svc_matrixreports] FOR LOGIN [DOMAIN\svc_matrixreports];
ALTER ROLE db_datareader ADD MEMBER [DOMAIN\svc_matrixreports];
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE TO [DOMAIN\svc_matrixreports];

-- or, a SQL login
CREATE LOGIN matrixreports WITH PASSWORD = '<strong-password>';
USE COSEC;
CREATE USER matrixreports FOR LOGIN matrixreports;
ALTER ROLE db_datareader ADD MEMBER matrixreports;
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE TO matrixreports;
```

`db_datareader` and nothing more. The `DENY` lines are belt and braces — that
role cannot write anyway — but they make the intent unmistakable to whoever
audits this later.

### Arranged in advance, not by you on the day

- **AnyDesk access** to the server, with unattended access set, so you can get
  back in after a reboot
- **Administrator rights** on the server, or somebody with them available
- **Approval to install the Microsoft ODBC driver** — send them
  [Appendix A](#appendix-a--what-gets-installed); it is the only thing we install
- **An anti-virus exclusion** for `C:\matrixreports`
- **The built application folder**, on your machine ready to transfer — see
  [Appendix B](#appendix-b--building-the-application-folder). Build it before
  the session, not during it

---

## 1. Connect and check the ground

Connect over AnyDesk, then in an **elevated PowerShell**:

```powershell
Get-ComputerInfo | Select-Object OsName, CsName, CsDomain
Get-Volume C | Select-Object SizeRemaining
w32tm /query /status
Get-TimeZone
```

You want at least 2 GB free on C:.

**The clock is what matters here.** Everything this tool reports is a time, so
a server whose clock or time zone is wrong produces attendance that is quietly,
plausibly wrong — far worse than obviously wrong, because nobody catches it.
Confirm the time zone **matches the Matrix server's**. Domain-joined servers
normally sync correctly; check anyway.

---

## 2. Prove the server can reach SQL Server

**Before installing anything.** If this fails, everything after it is wasted
effort.

```powershell
Test-NetConnection -ComputerName <sql-server-ip> -Port 1433
```

`TcpTestSucceeded : True` is what you want.

If it fails it is a firewall or a routing problem, and it is **their IT's to
fix** — do not try to work around it. If the instance is named
(`SQLHOST\COSEC`), UDP 1434 may also be needed; their DBA will know.

---

## 3. Install the ODBC driver

The only thing we install on their server.

Download the **x64 MSI** for *ODBC Driver 18 for SQL Server* from
<https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>
and run it.

**Check:**

```powershell
Get-OdbcDriver -Name "*SQL Server*" | Select-Object Name
```

`ODBC Driver 18 for SQL Server` must appear. Do not substitute FreeTDS or an
older driver — the date handling differs and it will produce wrong times.

---

## 4. Transfer the application

Zip `matrixreports-windows\` on your machine, send it with **AnyDesk's file
transfer**, and unzip on the server to:

```
C:\matrixreports\app
```

Around 60 MB, so give it a minute.

No installer, no Python — the interpreter and every library are inside the
executable.

**Check:**

```powershell
cd C:\matrixreports\app
.\matrixreports.exe --help          # lists discover, check, daily, ...
.\matrixreports.exe web --help      # the portal's options
```

> One executable does both jobs. `matrixreports.exe web` starts the portal;
> any other argument is the command line.

---

## 5. Point it at the database

This is where your three pieces of information go.

```powershell
cd C:\matrixreports\app
copy config\matrix-cosec-verified.example.yaml config\matrixreports.yaml
notepad config\matrixreports.yaml
```

**Leave the `schema:` block exactly as it is** — that is the verified mapping,
and it is the part that took longest to get right. Only edit `database:`.

**Windows Authentication** — note there is no password anywhere:

```yaml
database:
  driver: sqlserver
  dsn: "DRIVER={ODBC Driver 18 for SQL Server};SERVER=<ip-or-host>,1433;DATABASE=COSEC;Trusted_Connection=yes;TrustServerCertificate=yes"
```

**A SQL login:**

```yaml
database:
  driver: sqlserver
  dsn: "DRIVER={ODBC Driver 18 for SQL Server};SERVER=<ip-or-host>,1433;DATABASE=COSEC;UID=<user>;PWD=<password>;TrustServerCertificate=yes"
```

For a named instance use `SERVER=<host>\<instance>` and drop the `,1433`.

`TrustServerCertificate=yes` is needed because Driver 18 encrypts by default
and their SQL Server almost certainly has no certificate this machine trusts.
**The connection is still encrypted** — the certificate simply is not
validated. Worth saying in exactly those words if their security team asks,
because the option name reads worse than it is.

If you used a SQL login, lock the file down:

```powershell
icacls config\matrixreports.yaml /inheritance:r /grant "Administrators:R" "SYSTEM:R"
```

---

## 6. Confirm it reads the data correctly

```powershell
.\matrixreports.exe --config config\matrixreports.yaml check --from 2026-06-01 --to 2026-06-30
```

Use a month you know had people in the building. You want an employee count, a
punch count, and a breaks-per-day histogram.

| What you see | What it means |
| --- | --- |
| `Login failed for user` | Wrong credentials, or SQL Server is Windows-auth-only |
| `Data source name not found` | ODBC driver missing, or the 32-bit one was installed |
| `No punches found` | Wrong table, or no data in that range. Try another month |
| Employees but no punches | `employees.id` and `punches.emp_id` are different keys |
| **Every day shows 0 breaks** | Pointed at `Mx_DATDTrn`, the summary table. It must be `Mx_ATDEventTrn` |
| Many `DIRECTION_INFERRED` | `direction_in` / `direction_out` do not match `IOType` |

If the mapping looks wrong, let it work the schema out rather than guessing:

```powershell
.\matrixreports.exe --config config\matrixreports.yaml discover --write config\discovered.yaml
```

**Read the draft before using it.** On this schema `discover` reliably picks a
biometric-template table as the employee master and a constant column as the
direction — `docs/cosec-schema-verified.md` lists what to check.

---

## 7. Set the portal login

The portal **refuses to start on a network address without one**, by design.

```powershell
.\matrixreports.exe web --hash-password
```

It asks twice and prints a hash. Store it at machine level:

```powershell
[Environment]::SetEnvironmentVariable("MATRIXREPORTS_AUTH_USER", "hr", "Machine")
[Environment]::SetEnvironmentVariable("MATRIXREPORTS_AUTH_PASSWORD_HASH", "<the hash>", "Machine")
[Environment]::SetEnvironmentVariable("MATRIXREPORTS_CONFIG", "C:\matrixreports\app\config\matrixreports.yaml", "Machine")
```

**The plaintext password never goes on the server** — only the hash. Give the
password to whoever runs HR through a password manager, not over chat.

---

## 8. Start it at boot

Windows has no systemd. A Scheduled Task is native, needs no third-party
service wrapper, and nobody has to approve it.

Create `C:\matrixreports\start-portal.ps1`:

```powershell
Set-Location C:\matrixreports\app
& .\matrixreports.exe web --host 127.0.0.1 --port 8000
```

Register it — **from a new PowerShell window**, so it picks up the environment
variables from step 7:

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
             -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\matrixreports\start-portal.ps1"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 `
             -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

# Windows Authentication - MUST run as the service account, since that is the
# identity SQL Server sees:
Register-ScheduledTask -TaskName "MatrixReports Portal" -Action $action -Trigger $trigger `
  -Settings $settings -User "DOMAIN\svc_matrixreports" -Password "<password>" -RunLevel Highest

# SQL login instead - SYSTEM is fine:
# Register-ScheduledTask -TaskName "MatrixReports Portal" -Action $action -Trigger $trigger `
#   -Settings $settings -User "SYSTEM" -RunLevel Highest

Start-ScheduledTask -TaskName "MatrixReports Portal"
```

**Check:**

```powershell
Get-ScheduledTask -TaskName "MatrixReports Portal" | Select-Object State
(Invoke-WebRequest http://127.0.0.1:8000/ -SkipHttpErrorCheck).StatusCode
```

**`401` is the correct answer** — it means the login is working.

---

## 9. Make it reachable

Waitress is on loopback deliberately. How you expose it depends on where HR
sits.

**Inside their network only** — change `--host 127.0.0.1` to `--host 0.0.0.0`
in `start-portal.ps1`, restart the task, and open the port:

```powershell
New-NetFirewallRule -DisplayName "MatrixReports Portal" -Direction Inbound `
  -Protocol TCP -LocalPort 8000 -Action Allow -Profile Domain
```

HR then uses `http://<server-ip>:8000`.

**Reachable from outside their network — it needs TLS.** Do not skip it: the
login and every employee's hours would otherwise cross the network in clear
text. Put **IIS** in front (Web Server role, plus Application Request Routing
and URL Rewrite), proxy to `http://127.0.0.1:8000/`, and bind their
certificate. IIS is a Windows role, not third-party software, which is why it
is the right answer here.

**Get it in writing which of the two this is.** "It is only internal" is a
thing people say without checking.

---

## 10. Verify before handing over

```powershell
Restart-Computer
```

Reconnect over AnyDesk **without anyone touching the console**, then:

```powershell
Get-ScheduledTask -TaskName "MatrixReports Portal" | Select-Object State
```

The restart test is the one that matters. A portal that works until their next
patch Tuesday is not installed — and you will not be in the room for it.

Then in a browser:

- [ ] Open a **busy** day — use the **"Show the busiest day this month"** link.
      On a quiet day the report shows five OUT/IN groups, which is the minimum
      layout and proves nothing. A busy day shows eight, ten, more
- [ ] `1st In` and `Last Out` match Matrix's own report for a few employees
- [ ] The absent list is **plausible**. If a third of the company shows absent,
      visitor passes or `ATDCalcEnbl` are not filtered — see
      `docs/cosec-schema-verified.md`
- [ ] Excel and CSV download and open
- [ ] All six report types render
- [ ] A wrong password gives `401`

---

## 11. Leave these notes behind

- Server name, and the AnyDesk ID
- Which SQL identity it uses, and that it is **read-only**
- The portal URL, and who holds the HR password
- Restart: `Restart-ScheduledTask -TaskName "MatrixReports Portal"`
- Config: `C:\matrixreports\app\config\matrixreports.yaml`
- **Who to call when the numbers look wrong.** Nine times in ten it is their HR
  data rather than the tool: a visitor pass, an `ATDCalcEnbl` flag, a missing
  leaving date

---

## Troubleshooting

**`Login failed for user`** — with Windows Authentication the task is running
as the wrong account; check its identity in Task Scheduler. With a SQL login,
SQL Server may be in Windows-auth-only mode, which their DBA has to change.

**`Data source name not found and no default driver specified`** — the ODBC
driver is missing, or the 32-bit one was installed. `Get-OdbcDriver` should
list `ODBC Driver 18 for SQL Server`.

**`SSL Provider: certificate chain was issued by an authority that is not
trusted`** — expected on a LAN. Add `TrustServerCertificate=yes` to the `dsn`.

**The task shows Running but nothing answers** — run `start-portal.ps1` by hand
in a console and read the error. Usually `MATRIXREPORTS_CONFIG` was set after
the task was registered (re-register from a fresh PowerShell), or the service
account cannot read the config file.

**Everything is slow, or a step failed once then worked** — anti-virus. Get the
exclusion for `C:\matrixreports`.

**`refusing to bind 0.0.0.0 without authentication`** — step 7 was skipped.
Working as intended.

**The report shows only five groups** — that is the *minimum* layout, not a
limit. The day you opened simply had nobody taking more than five breaks. Use
the "Show the busiest day this month" link.

---

## What has been tested, and what has not

**Verified.** The compiled build was produced and driven end to end, not merely
configured: the portal served a daily report with ten OUT/IN groups and the
over-limit rows marked, streamed a real `.xlsx`, and the command-line half of
the same executable ran `check` and printed the histogram. The delivery folder
was then copied to an unrelated path and run from there — which is what exposes
a build that assumed where it lived. An automated test keeps the application
working under waitress.

That build was for macOS, because that is the machine available. **The
packaging configuration is proven; the Windows artifact is not.**

**Not tested**: everything Windows-specific — the Scheduled Task, the ODBC MSI,
IIS as a reverse proxy, Windows Authentication against SQL Server, and the
Windows build itself. The commands come from Microsoft's documentation and
ordinary practice.

**Build the Windows executable and run `--check` on it well before the
session.** Whether `pyodbc` compiles cleanly into a Windows build is the one
thing nobody can confirm until it is tried, and finding out mid-session with
their IT watching is the worst possible time.

---

## Appendix A — what gets installed

Send this to their IT ahead of the session. On a managed server this is the
conversation that gates everything else.

| Software | Source | Why |
| --- | --- | --- |
| ODBC Driver 18 for SQL Server | Microsoft, signed MSI | The only supported way to reach SQL Server |
| Our application — one folder, no installer | We supply it | The reports |
| *(only if TLS is needed)* IIS role + ARR + URL Rewrite | Microsoft | Reverse proxy and certificate |

**That is the entire list.** The application is a compiled build: the Python
interpreter and every library are inside the executable, so **no Python is
installed**, nothing goes into the registry or Program Files, and no package
downloads happen on their network.

Libraries compiled in, all mainstream:

```
Flask          web framework
waitress       WSGI server (pure Python, Windows-native)
pyodbc         SQL Server driver bindings
openpyxl       writes .xlsx
PyYAML         reads the config
               Jinja2, MarkupSafe, Werkzeug, click,
               blinker, itsdangerous, et_xmlfile   (pulled in by the above)
```

No scraping libraries, no telemetry, no analytics, no outbound calls.

### The questions their security team will ask

| Question | Answer |
| --- | --- |
| **Ports opened** | One: 8000, or 443 via IIS. Nothing else |
| **Outbound connections** | The Matrix SQL Server on 1433. Nothing else. No internet needed at runtime |
| **Does it write to our database?** | **No.** `db_datareader` with explicit `DENY` on every write, enforced by SQL Server rather than by our good behaviour |
| **Does it store our employee data?** | **No.** No database of our own, no cache, no sessions. Every request reads and closes. The only file we leave is a config |
| **What identity does it run as?** | A domain service account with read-only SQL access, or `SYSTEM` if a SQL login is used |
| **Where are credentials kept?** | With Windows Authentication, **nowhere** — that is the main reason to prefer it. Otherwise the SQL password sits in an ACL'd config file. The portal password is only ever a scrypt hash |
| **Uninstall** | Remove the scheduled task and delete `C:\matrixreports`. No registry keys, no installed runtime |

### One decision for them to make deliberately

If IIS is used, its logs record request URLs, which include the report type,
the date, and any employee codes filtered on. That is simultaneously the only
audit trail of who looked at whose attendance, and a plaintext file containing
employee codes. Some organisations require the first; some object to the
second. Ask which they want rather than leaving it as a default nobody looked
at.

---

## Appendix B — building the application folder

For us, before the session. **Nuitka does not cross-compile — a Windows `.exe`
needs a Windows machine.** Not their server: we do not install Python there.

```powershell
git clone <repo-url> matrixreports
cd matrixreports
python -m venv .venv
.\.venv\Scripts\pip install -e ".[web,sqlserver,build]"
.\.venv\Scripts\python scripts\build_exe.py --check
```

The result is **`dist\matrixreports-windows\`** — that is what gets zipped and
transferred in step 4. The config template and a short `READ-ME-FIRST.txt` are
already inside it.

Two things to watch:

- **Build with `[sqlserver]` installed.** The script skips driver packages that
  are not present and prints a warning. A build without `pyodbc` compiles
  perfectly and then cannot reach SQL Server at all — read the warnings rather
  than scrolling past them.
- **Nuitka also leaves `dist\launcher.dist\`.** Do not ship that one; it has no
  config template in it.

`--check` smoke-tests the result: that it starts, that both the command line
and the portal respond, and that the report templates were carried in.
Templates are the usual omission, because Flask loads them from disk at runtime
and nothing imports them.
