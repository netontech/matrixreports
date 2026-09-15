# Runbook — deploying on the client's Ubuntu VM

For the engineer doing the install on site. Follow it in order; every step has a
check, and if a check fails **stop there** rather than carrying on — a later
step will fail more confusingly.

Budget about 90 minutes, most of it waiting on downloads.

`docs/running-on-premise.md` covers the CLI-only Windows case. This one covers
an Ubuntu VM running the web portal against the live Matrix database.

---

## 0. Before you start — get these from their IT

Do not begin until all five are confirmed. Chasing them halfway through is
what turns a 90-minute job into a two-day one.

| What | Why | Check it yourself |
| --- | --- | --- |
| Ubuntu 22.04 or 24.04, 2 vCPU / 4 GB / 20 GB | Anything smaller and the portal competes with itself | `lsb_release -a`, `free -m`, `df -h /` |
| `sudo` on the VM | Every install step needs it | `sudo -v` |
| Network route to the Matrix SQL Server on **1433** | Without it nothing else matters | step 2 |
| A **read-only** SQL login | We never write. Ever | step 3 |
| Who reaches the portal, and from where | Decides HTTP-internal vs HTTPS-with-certificate | step 8 |

Also ask **which machine runs Matrix** and whether the SQL Server instance is
named (`HOST\INSTANCE`) or default. Named instances need the instance name in
the connection string and often a different port.

---

## 1. AnyDesk

Do this **first**. If the VM turns out to need a desktop installed, that is a
reboot, and you want to discover it before anything is running on the box.

> **AnyDesk needs a graphical session.** It is not an SSH replacement. A
> plain Ubuntu Server image has no desktop, and AnyDesk will install happily
> and then show a black screen or refuse to connect. Check before assuming.

```bash
# Is there a desktop at all?
ls /usr/share/xsessions/ 2>/dev/null || echo "NO DESKTOP INSTALLED"
```

**If there is no desktop**, install a light one (about 10 minutes):

```bash
sudo apt update
sudo apt install -y xfce4 xfce4-goodies lightdm
sudo systemctl enable lightdm
```

**Install AnyDesk:**

```bash
sudo apt install -y ca-certificates curl gnupg
curl -fsSL https://keys.anydesk.com/repos/DEB-GPG-KEY \
  | sudo gpg --dearmor -o /usr/share/keyrings/anydesk.gpg
echo "deb [signed-by=/usr/share/keyrings/anydesk.gpg] http://deb.anydesk.com/ all main" \
  | sudo tee /etc/apt/sources.list.d/anydesk.list
sudo apt update && sudo apt install -y anydesk
```

If the key or repo URL has moved, take them from
<https://anydesk.com/en/downloads/linux> rather than guessing.

> **Wayland breaks AnyDesk.** Ubuntu desktop images default to Wayland, and
> AnyDesk needs X11. If the VM has GNOME, force Xorg:
>
> ```bash
> sudo sed -i 's/^#\?WaylandEnable=.*/WaylandEnable=false/' /etc/gdm3/custom.conf
> sudo systemctl restart gdm3     # disconnects any graphical session
> ```
>
> On the xfce/lightdm setup above this does not apply — it is X11 already.

**Set unattended access** so we can get in without someone clicking Accept:

```bash
sudo systemctl enable --now anydesk
anydesk --get-id                                   # note this number down
sudo sh -c 'echo "<the-password>" | anydesk --set-password'
```

**Check:** connect from another machine using that ID and password, and confirm
you get a desktop rather than a black screen.

Record the ID and password somewhere we both can reach — a password manager,
not this document and not a chat message.

---

## 2. Prove the VM can reach SQL Server

Before installing anything else. If this fails, everything after it is wasted.

```bash
sudo apt install -y netcat-openbsd
nc -vz <matrix-sql-host> 1433
```

Expect `Connection to <host> 1433 port [tcp/ms-sql-s] succeeded!`

If it times out it is a firewall or a route, and it is their IT's to fix, not
something to work around. If it is refused, SQL Server may not be listening on
TCP, or it is a named instance on another port — ask them to confirm with
SQL Server Configuration Manager.

---

## 3. Ask for the read-only login

Send their DBA this, filling in a password. **We never need more than this**,
and asking for more is how a reporting tool ends up blamed for a data change.

```sql
CREATE LOGIN matrixreports WITH PASSWORD = '<strong-password>';
USE COSEC;
CREATE USER matrixreports FOR LOGIN matrixreports;
ALTER ROLE db_datareader ADD MEMBER matrixreports;
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE TO matrixreports;
```

The `DENY` lines are belt and braces — `db_datareader` alone cannot write —
but they make the intent unmistakable to whoever audits it later.

---

## 4. Install the ODBC driver and Python

```bash
sudo apt install -y curl gnupg python3-venv python3-pip git
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/microsoft.gpg
. /etc/os-release
echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/ubuntu/$VERSION_ID/prod $VERSION_CODENAME main" \
  | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt update
sudo ACCEPT_EULA=Y apt install -y msodbcsql18 unixodbc-dev
```

**Check:**

```bash
odbcinst -q -d          # must list [ODBC Driver 18 for SQL Server]
```

If Microsoft has no package for this Ubuntu release yet, use the previous
LTS's repo line — the driver is compatible. Do not substitute FreeTDS; the
date handling differs and it has bitten us.

---

## 5. Install the application

```bash
sudo useradd -r -m -d /opt/matrixreports -s /usr/sbin/nologin matrixreports
sudo -u matrixreports git clone <repo-url> /opt/matrixreports/app
cd /opt/matrixreports/app
sudo -u matrixreports python3 -m venv .venv
sudo -u matrixreports .venv/bin/pip install -e ".[web,sqlserver]"
```

It runs as its own unprivileged user that cannot log in. If the repo is
private, bring a deploy key or a tarball — do not paste a personal access
token onto their VM.

**Check:**

```bash
sudo -u matrixreports .venv/bin/python -c "import flask, pyodbc; print('ok')"
```

---

## 6. Point it at the database

```bash
sudo -u matrixreports cp config/matrix-cosec-verified.example.yaml \
                          config/matrixreports.yaml
sudo -u matrixreports nano config/matrixreports.yaml
```

Fill in `host`, `user`, `password`. Leave the whole `schema:` block alone — it
is already the verified mapping. Then:

```bash
sudo chmod 600 config/matrixreports.yaml     # it holds the password
```

**Check the connection and the mapping in one go:**

```bash
sudo -u matrixreports .venv/bin/matrixreports \
  --config config/matrixreports.yaml check --from 2026-06-01 --to 2026-06-30
```

You want an employee count, a punch count and a breaks-per-day histogram.

| What you see | What it means |
| --- | --- |
| `No punches found` | Wrong table, or the date range has no data. Try a month you know is busy |
| Employees but no punches | `employees.id` and `punches.emp_id` are different keys |
| Every day shows 0 breaks | Pointed at `Mx_DATDTrn`, the summary. It must be `Mx_ATDEventTrn` |
| Lots of `DIRECTION_INFERRED` | `direction_in` / `direction_out` do not match `IOType` |

**If the mapping looks wrong**, let it work the schema out rather than guessing:

```bash
sudo -u matrixreports .venv/bin/matrixreports \
  --config config/matrixreports.yaml discover --write config/discovered.yaml
```

Read the draft before using it. On our reference site `discover` picked a
biometric-template table as the employee master and a constant column as the
direction — see `docs/cosec-schema-verified.md` for what to check.

---

## 7. Set the login

The portal **refuses to start on a public address without one**, by design.

```bash
sudo -u matrixreports .venv/bin/matrixreports-web --hash-password
```

It prompts twice and prints a hash. Put it in a root-only environment file:

```bash
sudo mkdir -p /etc/matrixreports
sudo tee /etc/matrixreports/env >/dev/null <<'EOF'
MATRIXREPORTS_AUTH_USER=hr
MATRIXREPORTS_AUTH_PASSWORD_HASH=<paste the hash>
EOF
sudo chmod 600 /etc/matrixreports/env
```

The plaintext password never touches the VM. Give it to whoever runs HR
through a password manager.

---

## 8. Run it as a service

```bash
sudo tee /etc/systemd/system/matrixreports.service >/dev/null <<'EOF'
[Unit]
Description=Matrix attendance portal
After=network-online.target
Wants=network-online.target

[Service]
User=matrixreports
WorkingDirectory=/opt/matrixreports/app
EnvironmentFile=/etc/matrixreports/env
Environment=MATRIXREPORTS_CONFIG=/opt/matrixreports/app/config/matrixreports.yaml
ExecStart=/opt/matrixreports/app/.venv/bin/gunicorn \
  --bind 127.0.0.1:8000 --workers 3 --timeout 180 webapp.app:app
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/tmp

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now matrixreports
```

**Check:**

```bash
systemctl is-active matrixreports          # active
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/   # 401
```

`401` is correct — the login is working.

### Then put nginx in front

Bound to loopback, gunicorn is unreachable from the network on purpose.

```bash
sudo apt install -y nginx
sudo tee /etc/nginx/sites-available/matrixreports >/dev/null <<'EOF'
server {
    listen 80;
    server_name _;

    # Personal data: keep it out of caches and out of frames.
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    add_header Cache-Control "no-store" always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/matrixreports /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

**If it is reachable from outside their LAN it needs TLS**, not plain HTTP —
the login and every employee's hours would otherwise cross the network in
clear text. That needs a hostname pointing at the VM, then:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <hostname> --agree-tos --redirect
```

Inside their LAN only, HTTP is a defensible call — confirm which it is with
their IT and write the answer in the handover notes.

---

## 9. Verify before handing over

Work through all of these. A report that looks right on one day and is wrong
on another is the failure mode that costs trust.

```bash
# 1. The service survives a reboot
sudo reboot        # then, once back:
systemctl is-active matrixreports nginx

# 2. It is answering
curl -s -o /dev/null -w '%{http_code}\n' http://<vm-ip>/         # 401
curl -s -u hr:<password> -o /dev/null -w '%{http_code}\n' http://<vm-ip>/   # 200
```

Then in a browser:

- [ ] A **busy** day renders **more than 5** OUT/IN groups. Use the
      **"Show the busiest day this month"** link — on a quiet day five groups
      is the minimum layout and proves nothing
- [ ] `1st In` and `Last Out` match Matrix's own report for a few employees
- [ ] The absent list is **plausible**. If a third of the company shows absent,
      visitor passes or `ATDCalcEnbl` are not filtered — see
      `docs/cosec-schema-verified.md`
- [ ] Excel and CSV download and open
- [ ] All five report types render
- [ ] Wrong password gives `401`

---

## 10. Handover notes to leave behind

Write these down for whoever inherits it:

- VM hostname / IP, and the AnyDesk ID
- Which SQL login it uses, and that it is read-only
- The portal URL and who holds the HR password
- `sudo systemctl restart matrixreports` restarts it; logs are
  `sudo journalctl -u matrixreports -n 100`
- Config lives at `/opt/matrixreports/app/config/matrixreports.yaml`
- **Who to ask when the numbers look wrong** — usually HR data, not the tool:
  visitor passes, `ATDCalcEnbl`, and missing leaving dates

---

## Troubleshooting

**`Login failed for user`** — the SQL login is wrong, or SQL Server is set to
Windows authentication only. Their DBA has to enable mixed mode.

**`SSL Provider: certificate verify failed`** — ODBC Driver 18 encrypts by
default and their server has no trusted certificate. Add
`TrustServerCertificate=yes` to the `dsn`. Acceptable on a LAN; not a reason
to disable encryption entirely.

**Portal shows every employee absent** — the punch table mapping is wrong, or
the date has no data. Run `check` for a month you know is busy.

**Portal is slow on monthly/yearly** — expected on a large range; it reads
every punch. If it is painful, ask their DBA for an index on
`Mx_ATDEventTrn (UserID, Edatetime)`. **That is a change to their database —
get it in writing from their DBA, and do not create it yourself.**

**`refusing to bind 0.0.0.0 without authentication`** — step 7 was skipped.
Working as intended.

**`status=203/EXEC` / `Permission denied` running gunicorn** — almost always a
copied `.venv`. A virtualenv hard-codes absolute paths in its shebangs, so one
brought over in a tarball still points at wherever it was built, and the new
user cannot reach that path. Delete it and build a fresh one:

```bash
sudo rm -rf /opt/matrixreports/app/.venv
sudo -u matrixreports python3 -m venv /opt/matrixreports/app/.venv
sudo -u matrixreports /opt/matrixreports/app/.venv/bin/pip install -e "/opt/matrixreports/app[web,sqlserver]"
```

A clean `git clone` never hits this; a tarball of a working install does.

**The service starts by hand but not under systemd** — usually `ProtectHome`.
The unit sets it, so an app installed under `/home/...` is invisible to it.
Install under `/opt` as above, or drop that line.

---

## What has been tested, and what has not

Worth knowing which parts of this are verified and which are from
documentation:

**Verified on an Ubuntu 24.04 VM**, with the exact unit file above: the
service starts, answers `401` unauthenticated and `200` authenticated, reads
the database, and `.xlsx` downloads work under `ProtectSystem=strict` — the
directive most likely to break them. The `--hash-password` flow, including its
length and mismatch checks. The AnyDesk signing key, the AnyDesk apt repo, and
the Microsoft ODBC repos for both 22.04 and 24.04 all resolve.

**Not tested**: AnyDesk itself, end to end. The machine available for testing
is headless, which is the exact condition under which AnyDesk fails — so treat
step 1 as the part most likely to need improvisation, and do it first.
