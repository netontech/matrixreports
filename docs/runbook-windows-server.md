# Runbook — deploying on the client's Windows Server

For the engineer doing the install on site. Follow it in order; every step has
a check, and if a check fails **stop there** rather than carrying on.

Budget about 90 minutes.

The server is built to the client's own standard — domain-joined, patched,
with their anti-virus. That is a better place to be than a VM we built, but it
means **we adapt to their build rather than the other way round**: expect an
approval for every piece of software, and ask early.

`docs/runbook-ubuntu-vm.md` is the Linux equivalent, kept for reference.
`docs/running-on-premise.md` covers running the reports from the command line
with no portal.

---

## 0. Before you start — get these from their IT

Do not begin until all of these are settled. On a managed, AV'd server the
approvals take longer than the work.

| What | Why |
| --- | --- |
| Windows Server 2019 or 2022, 2 vCPU / 8 GB / 60 GB | The portal, plus room for their agents |
| **Administrator rights**, or someone with them on call | Every install step needs elevation |
| **Approval to install Python and the ODBC driver** | See [Appendix A](#appendix-a--what-gets-installed). Both are Microsoft-signed or python.org-signed |
| Network route to the Matrix SQL Server on **1433** | Step 2 |
| **How we authenticate to SQL** — Windows or SQL login | Step 3. Ask; the answer changes the config |
| **How we get remote access** — RDP or AnyDesk | Step 1 |
| Who reaches the portal, and from where | Decides HTTP-internal vs HTTPS |
| **AV exclusion** for the install folder | Step 1c — otherwise expect odd, intermittent failures |

Also ask **which machine runs Matrix**, and whether the SQL Server instance is
named (`HOST\INSTANCE`) or default. Named instances need the instance name and
often a different port.

---

## 1. Access and ground rules

### 1a. Remote access — ask before installing anything

Windows Server already has **RDP**, and in a managed estate that is almost
certainly what their IT will want us to use. It is built in, already governed
by their policy, and needs no approval.

**Ask first. Do not install AnyDesk on a managed server on your own
initiative** — on their standard build it is unapproved software on a domain
member, and installing it unasked is the kind of thing that sours a project.

If they do want AnyDesk, it is a normal MSI from
<https://anydesk.com/en/downloads/windows> and none of the Linux complications
apply: no Wayland, no autologin, no desktop to install. Set unattended access
in Settings → Security.

**Check:** you can reach the server the agreed way, and get back in after a
reboot.

### 1b. Confirm the build

In an elevated PowerShell:

```powershell
Get-ComputerInfo | Select-Object OsName, OsVersion, CsName, CsDomain
Get-Volume C | Select-Object SizeRemaining
[Environment]::Is64BitOperatingSystem
```

### 1c. Ask for an AV exclusion, now

Their anti-virus will scan every file Python touches. In the worst case it
quarantines something mid-install; more often it just makes everything slow and
occasionally fails a step for no visible reason.

Ask for an exclusion on the install folder:

```
C:\matrixreports
```

If they will not grant one, carry on — but if a later step fails oddly and
then works on retry, this is the first thing to suspect.

### 1d. Check the clock

Everything this tool reports is a time. A server whose clock has drifted
produces attendance that is quietly, plausibly wrong, which is worse than
obviously wrong.

```powershell
w32tm /query /status
Get-TimeZone
```

Domain-joined servers normally sync from the domain controller and are fine.
Confirm the **time zone matches the Matrix server's** — if the two disagree,
every report is shifted.

---

## 2. Prove the server can reach SQL Server

Before installing anything. If this fails, everything after it is wasted.

```powershell
Test-NetConnection -ComputerName <matrix-sql-host> -Port 1433
```

`TcpTestSucceeded : True` is what you want.

If it fails it is a firewall or a route, and it is their IT's to fix. If the
instance is named, `SQL Server Browser` on UDP 1434 may also be needed — their
DBA will know.

---

## 3. Decide how we authenticate, then get it set up

Two options. **Ask their DBA which they prefer** — most managed estates prefer
the first, and it is better for us too.

### Option A — Windows Authentication (preferred)

A domain service account, granted read access. **No password is stored
anywhere in our configuration**, which removes a whole category of audit
question.

Ask their DBA for a service account, e.g. `DOMAIN\svc_matrixreports`, and:

```sql
CREATE LOGIN [DOMAIN\svc_matrixreports] FROM WINDOWS;
USE COSEC;
CREATE USER [DOMAIN\svc_matrixreports] FOR LOGIN [DOMAIN\svc_matrixreports];
ALTER ROLE db_datareader ADD MEMBER [DOMAIN\svc_matrixreports];
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE TO [DOMAIN\svc_matrixreports];
```

The service must then **run as that account** (step 7).

### Option B — a SQL login

If they do not do service accounts:

```sql
CREATE LOGIN matrixreports WITH PASSWORD = '<strong-password>';
USE COSEC;
CREATE USER matrixreports FOR LOGIN matrixreports;
ALTER ROLE db_datareader ADD MEMBER matrixreports;
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE TO matrixreports;
```

Either way: **`db_datareader` and nothing more.** The `DENY` lines are belt and
braces — that role cannot write — but they make the intent unmistakable to
whoever audits this later.

---

## 4. Install Python and the ODBC driver

**Python 3.12** from <https://www.python.org/downloads/windows/> — the 64-bit
installer. In the installer:

- **Tick "Add python.exe to PATH"**
- Choose **"Install for all users"**, so a service account can use it

**ODBC Driver 18 for SQL Server** — the x64 MSI from Microsoft:
<https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>

**Check**, in a *new* PowerShell window so PATH is picked up:

```powershell
python --version                       # 3.12.x
Get-OdbcDriver -Name "*SQL Server*" | Select-Object Name
```

You need `ODBC Driver 18 for SQL Server` in that list. Do not substitute
FreeTDS or an older driver; the date handling differs.

---

## 5. Install the application

```powershell
mkdir C:\matrixreports
cd C:\matrixreports
# Either clone, or copy in the files we bring
git clone <repo-url> app
cd app
python -m venv .venv
.\.venv\Scripts\pip install -e ".[web,sqlserver]"
```

If they have no internet access on the server, bring the wheels — see
"If the machine has no internet access" in `docs/running-on-premise.md`.

**Check:**

```powershell
.\.venv\Scripts\python -c "import flask, pyodbc, waitress; print('ok')"
```

> **Note for anyone who has done the Linux install**: on Windows the server is
> **waitress**, not gunicorn. Gunicorn forks, and there is no `fork()` on
> Windows, so it cannot run there at all. `pip install -e ".[web]"` already
> pulls waitress.

---

## 6. Point it at the database

```powershell
copy config\matrix-cosec-verified.example.yaml config\matrixreports.yaml
notepad config\matrixreports.yaml
```

Leave the whole `schema:` block alone — it is the verified mapping. Set the
`database` block to match step 3:

**Option A — Windows Authentication.** No password anywhere:

```yaml
database:
  driver: sqlserver
  dsn: "DRIVER={ODBC Driver 18 for SQL Server};SERVER=<host>,1433;DATABASE=COSEC;Trusted_Connection=yes;TrustServerCertificate=yes"
```

**Option B — a SQL login:**

```yaml
database:
  driver: sqlserver
  dsn: "DRIVER={ODBC Driver 18 for SQL Server};SERVER=<host>,1433;DATABASE=COSEC;UID=matrixreports;PWD=<password>;TrustServerCertificate=yes"
```

`TrustServerCertificate=yes` is needed because Driver 18 encrypts by default
and their SQL Server probably has no certificate your server trusts. The
connection is still encrypted; the certificate simply is not validated.
Acceptable on a LAN — and worth saying to their security team in those words,
because "TrustServerCertificate" reads worse than it is.

If using a SQL login, restrict the file so only administrators and the service
account can read it:

```powershell
icacls config\matrixreports.yaml /inheritance:r /grant "Administrators:R" "SYSTEM:R"
```

**Check the connection and the mapping in one go:**

```powershell
.\.venv\Scripts\matrixreports --config config\matrixreports.yaml check --from 2026-06-01 --to 2026-06-30
```

You want an employee count, a punch count and a breaks-per-day histogram.

| What you see | What it means |
| --- | --- |
| `Login failed for user` | Wrong credentials, or SQL Server is set to Windows-auth only |
| `No punches found` | Wrong table, or that range has no data. Try a month you know is busy |
| Employees but no punches | `employees.id` and `punches.emp_id` are different keys |
| Every day shows 0 breaks | Pointed at `Mx_DATDTrn`, the summary. It must be `Mx_ATDEventTrn` |
| Lots of `DIRECTION_INFERRED` | `direction_in` / `direction_out` do not match `IOType` |

If the mapping looks wrong, let it work the schema out rather than guessing:

```powershell
.\.venv\Scripts\matrixreports --config config\matrixreports.yaml discover --write config\discovered.yaml
```

Read the draft before using it — `docs/cosec-schema-verified.md` lists the
roles `discover` gets wrong on this schema.

---

## 7. Set the login and run it as a service

### 7a. The portal login

The portal **refuses to start on a public address without one**, by design.

```powershell
.\.venv\Scripts\matrixreports-web --hash-password
```

It prompts twice and prints a hash. The plaintext never goes on the server —
give it to whoever runs HR through a password manager.

Store the hash as a **machine-level** environment variable:

```powershell
[Environment]::SetEnvironmentVariable("MATRIXREPORTS_AUTH_USER", "hr", "Machine")
[Environment]::SetEnvironmentVariable("MATRIXREPORTS_AUTH_PASSWORD_HASH", "<paste the hash>", "Machine")
[Environment]::SetEnvironmentVariable("MATRIXREPORTS_CONFIG", "C:\matrixreports\app\config\matrixreports.yaml", "Machine")
```

### 7b. Run it at startup

Windows has no systemd. Use a **Scheduled Task**, which is native, needs no
third-party service wrapper, and is something their IT will accept without a
conversation.

Create `C:\matrixreports\start-portal.ps1`:

```powershell
Set-Location C:\matrixreports\app
& .\.venv\Scripts\python.exe -m waitress `
    --host=127.0.0.1 --port=8000 --threads=8 webapp.app:app
```

Register it:

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
             -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\matrixreports\start-portal.ps1"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
             -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit ([TimeSpan]::Zero)

# Option A: run as the domain service account (Windows Authentication)
Register-ScheduledTask -TaskName "MatrixReports Portal" -Action $action -Trigger $trigger `
  -Settings $settings -User "DOMAIN\svc_matrixreports" -Password "<password>" -RunLevel Highest

# Option B, if using a SQL login instead:
# Register-ScheduledTask -TaskName "MatrixReports Portal" -Action $action -Trigger $trigger `
#   -Settings $settings -User "SYSTEM" -RunLevel Highest

Start-ScheduledTask -TaskName "MatrixReports Portal"
```

**The account matters.** With Windows Authentication the task *must* run as the
service account, because that is the identity SQL Server sees.

**Check:**

```powershell
Get-ScheduledTask -TaskName "MatrixReports Portal" | Select-Object State
(Invoke-WebRequest http://127.0.0.1:8000/ -SkipHttpErrorCheck).StatusCode   # 401
```

`401` is correct — the login is working.

### 7c. Make it reachable

Waitress is bound to loopback on purpose. How you expose it depends on the
answer from step 0.

**Internal only, plain HTTP** — simplest. Bind waitress to the LAN instead, by
changing `--host=127.0.0.1` to `--host=0.0.0.0` in the script, and open the
port:

```powershell
New-NetFirewallRule -DisplayName "MatrixReports Portal" -Direction Inbound `
  -Protocol TCP -LocalPort 8000 -Action Allow -Profile Domain
```

**Reachable from outside their LAN — it needs TLS.** Waitress does not do TLS,
so put **IIS** in front of it: add the *Web Server (IIS)* role, install
*Application Request Routing* and *URL Rewrite*, enable proxying, and add a
rewrite rule to `http://127.0.0.1:8000/`. Bind their certificate in IIS.

IIS is a Windows Server role rather than third-party software, which is why it
is the right answer here even though it is heavier than nginx.

Without TLS, the login and every employee's hours cross the network in clear
text. Do not let "it is only internal" decide this by default — get it in
writing which it is.

---

## 8. Verify before handing over

```powershell
Restart-Computer          # then, once back:
Get-ScheduledTask -TaskName "MatrixReports Portal" | Select-Object State
```

The restart test is the important one: a portal that works until the next
patch Tuesday is not deployed.

Then in a browser:

- [ ] A **busy** day renders **more than 5** OUT/IN groups. Use the
      **"Show the busiest day this month"** link — on a quiet day five groups
      is the minimum layout and proves nothing
- [ ] `1st In` and `Last Out` match Matrix's own report for a few employees
- [ ] The absent list is **plausible**. If a third of the company shows absent,
      visitor passes or `ATDCalcEnbl` are not filtered — see
      `docs/cosec-schema-verified.md`
- [ ] Excel and CSV download and open
- [ ] All six report types render
- [ ] Wrong password gives `401`

---

## 9. Handover notes to leave behind

- Server name, and how to reach it (RDP or AnyDesk)
- Which SQL identity it uses, and that it is read-only
- The portal URL, and who holds the HR password
- Restart: `Restart-ScheduledTask -TaskName "MatrixReports Portal"`
- Config: `C:\matrixreports\app\config\matrixreports.yaml`
- **Who to ask when the numbers look wrong** — usually HR data, not the tool:
  visitor passes, `ATDCalcEnbl`, and missing leaving dates

---

## Troubleshooting

**`Login failed for user`** — with Windows Authentication, the task is running
as the wrong account; check the task's identity. With a SQL login, SQL Server
may be in Windows-auth-only mode, which their DBA has to change.

**`Data source name not found`** — the ODBC driver is missing, or you installed
the 32-bit one against 64-bit Python. `Get-OdbcDriver` should list
`ODBC Driver 18 for SQL Server`.

**`SSL Provider: certificate chain was issued by an authority that is not
trusted`** — expected on a LAN. Add `TrustServerCertificate=yes` to the `dsn`.

**The task shows Running but nothing answers** — look at the task's last
result, and run `start-portal.ps1` by hand in a console to see the error.
Usually `MATRIXREPORTS_CONFIG` is not set at machine level, or the service
account cannot read the config file.

**Everything is slow, or a step failed once then worked** — anti-virus. Get the
exclusion from step 1c.

**`refusing to bind 0.0.0.0 without authentication`** — step 7a was skipped.
Working as intended.

**Portal shows every employee absent** — wrong punch table, or the date has no
data. Run `check` for a month you know is busy.

---

## What has been tested, and what has not

**Verified**: the application runs correctly under **waitress**, serving
reports and streaming `.xlsx` downloads, with an automated test covering it so
it cannot regress. The Windows-only switch from gunicorn to waitress is in the
package, not just in this document.

**Not tested**: everything Windows-specific — the Scheduled Task, the ODBC MSI,
IIS as a reverse proxy, and Windows Authentication against SQL Server. No
Windows machine was available. The commands are from Microsoft's documentation
and ordinary practice, but treat step 7b as the part most likely to need
adjusting on the day, and leave time for it.

---

## Appendix A — what gets installed

Give this to their IT. On a managed server this is the conversation that
gates everything else, so send it ahead of the visit.

| Software | Source | Why |
| --- | --- | --- |
| Python 3.12 (64-bit) | python.org, signed | Runs the application |
| ODBC Driver 18 for SQL Server | Microsoft, signed MSI | The only supported way to reach SQL Server |
| Our application + 11 Python packages | In an isolated virtualenv under `C:\matrixreports` | The reports |
| *(optional)* IIS role + ARR + URL Rewrite | Microsoft | Only if the portal needs TLS |
| *(optional)* AnyDesk | anydesk.com | Only if they do not want us using RDP |

The Python packages, all mainstream:

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
| **Outbound connections** | The Matrix SQL Server on 1433. Nothing else. No internet needed at runtime, only during install |
| **Does it write to our database?** | **No.** `db_datareader` with explicit `DENY` on every write, enforced by SQL Server, not by our good behaviour |
| **Does it store our employee data?** | **No.** No database of our own, no cache, no sessions. Every request reads and closes. The only file we leave is a config |
| **What identity does it run as?** | A domain service account with read-only SQL access — or `SYSTEM` if they prefer a SQL login |
| **Where are credentials kept?** | With Windows Authentication, **nowhere** — that is the main reason to prefer it. Otherwise the SQL password sits in an ACL'd config file. The portal password is only ever a scrypt hash in a machine environment variable |
| **Uninstall** | Remove the scheduled task, delete `C:\matrixreports`, uninstall Python and the ODBC driver if nothing else uses them |

### One decision to make deliberately

If IIS is used, its logs record request URLs, which include the report type,
the date, and any employee codes filtered on. That is simultaneously the only
audit trail of who looked at whose attendance and a plaintext file containing
employee codes. Some organisations require the first; some object to the
second. Ask which they want rather than leaving it as a default nobody
examined.
