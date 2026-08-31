# Prompt for a session on the server that holds the SQL files

Copy everything in the block below into a Claude Code session running on the
machine where the Matrix SQL files are. Replace the two bracketed placeholders
first.

---

```text
The repo https://github.com/netontech/matrixreports (branch
claude/matrix-attendance-n-inouts-6on2no) contains a Python package that builds
attendance reports from a Matrix COSEC database. It exists because the stock
Matrix report flattens each day into six fixed IN/OUT slots and silently drops
everything past the fifth break, which also overstates hours worked for anyone
who steps out more often. This package reads raw punch rows and pairs them with
no cap. Read its README.md, docs/findings.md and docs/schema-mapping.md first.

The Matrix SQL files are at [PATH TO THE SQL FILES ON THIS MACHINE].

Your job is to produce a verified schema mapping and a first set of reports.

1. Clone the repo, check out that branch, and `pip install -e ".[dev]"`.
   Run `pytest` to confirm the suite is green before changing anything.

2. Work out the schema from the SQL files:

       matrixreports discover --sql-file <each .sql file> --write config/discovered.yaml

   This parses the CREATE TABLE statements; it needs no database connection.
   Then read the console output carefully and tell me:
     - which table it chose as the punch log, and its column mapping;
     - whether it reported any PRE-FLATTENED SUMMARY TABLE (one with IN1, OUT1,
       IN2, OUT2 ... columns). If it did, say how many slot columns it has.
       That table is almost certainly what the stock report reads, and its width
       is the origin of the six-in/out limit. Confirming this is the single most
       useful thing you can tell me.
     - what values it found for direction_in / direction_out.

3. Sanity-check the mapping by hand against the dump. In particular:
     - The punch table must have roughly (employees x punches per day x days)
       rows, NOT (employees x days). If it looks like the latter, discovery
       picked a summary table; choose one of the alternatives it listed.
     - direction_in / direction_out must not be reversed. Reversing them turns
       time at work into time out and produces plausible-looking nonsense.

4. If the dump contains data (INSERT statements), load it into a local SQL
   Server, MySQL or SQLite instance and run against it:

       matrixreports --config config/discovered.yaml check --from <date> --to <date>

   Report the breaks-per-day histogram it prints, especially how many day
   records carry more than 5 breaks. If the dump is schema-only, say so and
   skip to step 6.

5. Generate reports for a day that also appears in the client's existing
   spreadsheets, and compare row by row:

       matrixreports --config config/discovered.yaml daily --date <date>

   `1st In` and `Last Out` should match the existing report exactly. The break
   columns are where this tool shows more. Where `Actual Works Hours` differs,
   work out why and tell me — the expected cause is breaks the old report could
   not fit, but I want that confirmed rather than assumed.

6. Report back with: the schema mapping, the flattened-table finding, any
   discrepancy from step 5, and anything in the schema this package does not yet
   handle (multiple shifts per employee, shift rosters, manual attendance
   corrections, overtime rules).

Constraints:
- Do NOT commit the SQL dump, any extract of employee data, or any credentials.
  The repo is public. Commit only code, config templates with placeholder
  values, and findings written in prose.
- The tool must only ever read from the database. No writes.
- Do not change the pairing logic to match the old report's numbers. Where they
  disagree, the old report is the one that is wrong; explain the difference
  instead of reproducing it.
```

---

## If you would rather not run a second session

Send me the output of this command instead — it prints table and column names
only, no employee data:

```bash
matrixreports discover --sql-file Cosec.sql
```

Or, if you have not installed anything yet, just the DDL:

```bash
grep -iE "CREATE TABLE" Cosec.sql
```

Either is enough for me to finish the mapping.
