# The COSEC schema, verified

Notes from mapping this package against a real Matrix COSEC database (551
tables) and reconciling a day of its output against the client's existing
spreadsheet. `docs/schema-mapping.md` explains how to do the mapping; this
records what the mapping turned out to be, and what the data then showed.

No employee data appears here. Every figure is an aggregate.

## The mapping

| Role | Table | Rows |
| --- | --- | --- |
| Employee master | `Mx_UserMst` | 60 |
| Raw punch log | `Mx_ATDEventTrn` | 119,800 |
| Pre-flattened summary | `Mx_DATDTrn` | 24,482 |
| Shift master / roster | `Mx_ShiftMst` / `Mx_ShiftSchMst` / `Mx_ShiftSchDet` | 5 / 99 / 3,168 |
| Holiday master | `Mx_HolidaySchMst` | 30 |

```yaml
punches:
  table: Mx_ATDEventTrn
  columns: {emp_id: UserID, timestamp: Edatetime, direction: IOType, device: MID}
direction_in:  ["1"]
direction_out: ["0"]
```

The full mapping is in `config/matrix-cosec-verified.example.yaml`.

## `Mx_DATDTrn` is where the ceiling comes from

Its punch columns are `Punch1 … Punch12`, plus a separate `OutPunch`, with
`SPFID1..12` and `P1TYPE/P1MID/P1DID …` alongside.

**Twelve slots is exactly the stock report's layout**: `1st In` + five
(`OUT`, `IN`) pairs + `Last Out` = 1 + 10 + 1 = 12. The six numbered groups on
the sheet and the twelve `Punch` columns in this table are the same structure.
A day needing a thirteenth punch has nowhere to put it.

The table holds one row per employee per day (24,482 rows over 59 users and 607
dates); the raw log holds 119,800 rows over 60 users and 550 dates, about 3.6
punches per employee-day. That ratio — a little under five to one — is what
distinguishes the log from the summary.

### `discover` used to miss it

`FLATTENED_RE` matched only direction-named slots (`IN1`, `OUT1`, `I1`, `O1`).
Matrix numbers its slots instead, so on a real COSEC database the warning never
fired — on the one schema it exists for. The pattern now also matches
`PUNCH<n>`, covered by a regression test in `tests/test_discover.py`.

Anything of this shape is still never selected as a punch source.

## Direction

`IOType` is the direction column: `1` = entry, `0` = exit. Confirmed three ways,
because reversing it turns time at work into time out and still looks plausible:

1. A single employee-day traces a clean `1,0,1,0 …`, opening on `1` at the start
   of the shift and closing on `0` at the end.
2. Across the database the first read of a day is predominantly `1` and the last
   predominantly `0`.
3. The readers are direction-dedicated: the entry readers emit `1` and the exit
   readers emit `0`, and the totals reconcile exactly (58,421 / 61,379).

Two traps. `Type` — which name-based scoring is drawn to — is a **constant `21`**
on all 119,800 rows and is not a direction at all. And `DID` is a door id, also
constant; the reader is `MID`.

`discover` also mis-picks the employee master, preferring a biometric-template
table over `Mx_UserMst`, and reports the holiday master as absent though
`Mx_HolidaySchMst` exists. Check all four roles before trusting the draft.

## What the data shows

12,261 day records carry punches. Breaks per day:

| Breaks | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Days | 1064 | 1118 | 1685 | 2329 | 2549 | 1855 | 1041 | 417 | 131 | 53 | 12 | 4 | 3 |

**1,661 records — 13.5% — carry more than five breaks** and cannot be expressed
by the stock report. The distribution peaks at four and tapers, as a real one
should. The client's sheet for the sampled day does the opposite: it climbs to a
spike at exactly five, where nearly half the workforce sits, and stops dead.
A distribution that rises into its maximum and terminates there is censored, not
observed.

## Reconciling against the existing report

For one day checked row by row against the client's raw Matrix export:

- `1st In` matched on 34 of 35 employees, `Last Out` on 34 of 35.
- The two mismatches are both errors in the old report. In one, the day's first
  punch was taken from an **exit** reader and reported as the `1st In`. In the
  other, the final punch of the day is an **entry** — an unclosed session — and
  was reported as the `Last Out`. This package reports the first true entry, and
  flags the unclosed session as `MISSING_OUT` rather than silently using it.
- Nine employees showed more breaks than the old report could hold. All nine sat
  at exactly five there.

Where `Actual Works Hours` differs there are four distinct causes, and only the
first is the cap:

1. **Breaks that did not fit.** The worst case dropped a 2h49m break and a 2h00m
   break from the same day, overstating that day by 237 minutes.
2. **The `hh.mm` decimal defect** (`docs/findings.md` §2). One employee with only
   five breaks — not truncated — had four minutes deducted where the sheet's own
   `Total Out` reads `1:04`: exactly one hour lost to reading `1.04` as minutes.
3. **Missing-punch policy**, on the two rows above.
4. **Rounding**, 0–7 minutes on clean rows, because the old columns are
   minute-rounded while punches carry seconds.

The mechanism behind (1) is arithmetic rather than inference. On every row of
the sheet, `Total Out Minutes` equals the sum of the displayed `MINS` cells
exactly, and `Actual Works Hours` equals elapsed time minus `Total Out Minutes`.
A sixth break is therefore never in `Total Out`, and so is never subtracted.
**The overstatement is entailed by the cap, not incidental to it.**

## Not yet handled

Roughly in order of how much they matter.

1. **Multiple shifts.** `shift:` is a single global window. The verified site runs
   four (`G2` 15,168 days, `GS` 5,109, `S2` 2,535, `S1` 851), so about 30% of day
   records get `Late IN` / `Early OUT` / `Late OUT` measured against the wrong
   shift. The per-day scheduled shift is in `Mx_DATDTrn.SchSFT` (`WrkSFT` for the
   worked one).
2. **Manual attendance corrections.** 514 day records carry `MANENT > 0` with
   `ManEntCorrBy` / `ManEntCorrDate`, manually inserted punches in
   `ManualRINId1..6` / `ManualROUTId1..6`, and 25 `MANABS`. **These live only in
   `Mx_DATDTrn`, never in the raw event log.** A corrected day will legitimately
   differ between the two sources, and this package reads only the log — so a
   correction an administrator made will not be reflected. This needs a decision
   rather than a default.
3. **Shift rosters.** `Mx_ShiftSchMst` / `Mx_ShiftSchDet` are not read.
4. **Cross-midnight shifts.** Two shift codes look like night shifts;
   `pairing.day_start_hour` needs setting for them. The figures above use
   calendar days.
5. **Holidays.** `Mx_HolidaySchMst` is not picked up, so `HOL` days and the
   attendance percentage stay empty.
6. **Paired-reader double reads.** With separate entry and exit readers, 402
   same-direction pairs occur within 120s **across two different readers**. The
   duplicate rule keys on a 60s window, so some are not collapsed and surface as
   `CONSECUTIVE_IN` / `CONSECUTIVE_OUT`.
7. **Overtime.** `Overtime`, `AUTHOT`, `MANOT` and `OTRID` are zero across the
   whole database, so overtime is not in use at this site and the absence of OT
   rules costs nothing today. The schema supports it if that changes.
