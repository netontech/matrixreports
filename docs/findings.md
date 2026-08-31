# What the supplied reports show

Notes from reading the four workbooks provided with this request. They are the
reason the package works the way it does.

| File | Sheets read |
| --- | --- |
| `June_12026.xls` | `Sheet` (raw Matrix export), `Sheet (2)` (working copy), `Summary (2)` (hand-built daily summary) |
| `WEEKLY_ATTENDANCE_APRIL__JULY_2026.xls` | `Sheet0`, `Highest to Lowest` |
| `Monthly_Attendance_2026_.xlsx` | `June`, `May`, `APRIL`, `March`, `FEB`, `Jan`, `Jan Sum`, `Yearly 2026` |
| `Yearly_Attendance_Report.xlsx` | `2025-2026`, `Yearly 2025` |

## 1. The six in/out ceiling is a layout limit, not a data limit

The raw Matrix export (`June_12026.xls`, sheet `Sheet`) lays a day out as:

```
1st In | Last Out | [OUT IN MINS] x5 | OUT | No. Of OUT | Total Out Minutes | ...
                    ^ group 1..5       ^ group 6
```

Six numbered groups, of which five carry an `OUT`/`IN`/`MINS` triple and the
sixth carries only the final `OUT`. So the sheet can express **at most five
breaks**. Row 8 of `Sheet (2)` (`Alben Godlin`) fills all five and reports
`No. Of OUT = 5`; so does row 10, row 13, row 17, row 19 and eleven others. That
clustering at exactly 5 is the signature of a cap, not of behaviour.

The consequence is not only a missing column. `Total Out Minutes`,
`Actual Works Hours` and `Wrk Hrs + Out Time` are all derived from the visible
groups, so for anyone who stepped out a sixth time the report **overstates hours
worked** by the length of every break it could not show.

Nothing is wrong with the stored data — this is why the database looks complete
when it is checked directly. Run `matrixreports check` to see the distribution of
breaks per day and exactly how many day records exceed five.

## 2. Durations are stored as `hh.mm` decimals and then summed

Throughout the monthly and weekly sheets a duration is written as a number whose
integer part is hours and whose fractional part is minutes: `8.14` for 8h14m.
Those numbers are then added with `SUM()`.

That arithmetic is wrong, because the fractional part is base 60 while `SUM`
treats it as base 100:

| Cells | Reported | Correct |
| --- | --- | --- |
| `Highest to Lowest!AB6 + AC6` (48.49 + 52.45) | `100.94` | `101:34` |
| Any two of `8.14 + 8.50` | `16.64` | `17:04` |

Every `TOTAL HOURS` column in the weekly workbook and every monthly total such
as `June!AH17 = 182.3` carries this error, and it compounds with the number of
cells summed. The error is always in the same direction — decimal sums
under-count once the minutes exceed `.60`, then over-count as the carry is lost.

This package never produces such a value. Durations are `timedelta` internally
and are written to Excel as time serials with a `[h]:mm` number format, so the
cell displays `08:14`, still sums correctly inside the sheet, and does not wrap
at 24 hours.

## 3. One column, several types

`Highest to Lowest` mixes text and numbers in the same column: `Q6 = '54:00'`
(text) sits above `Q10 = 45.0` (number), and `AG` is text `'38:14'` while `AH`
is the number `55.17`. Excel sorts and sums these inconsistently — text is
ignored by `SUM` entirely, so a total over a mixed column silently omits rows.

## 4. Missing data and zero are indistinguishable

`Sheet (2)` writes `':'` into the `MINS` cells of unused groups and `00:00` into
`Total Out Minutes` for employees on leave (rows 16, 29, 31, 33). A day on sick
leave and a day where somebody worked without breaks both read as `00:00`.
Here a duration that does not exist is left blank, and the status (`Sick Leave`,
`OFF`, `HOL`, `A`) goes in its own column.

## 5. A last-out that lost its date

`Sheet (2)!D21` (`Devyani Gandhi`) holds `1899-12-31 19:13` — a time with no
date, i.e. an Excel time serial written into a datetime cell. `Early OUT` for
that row is then computed as `01:22`, which is not what the punches say. Storing
full datetimes, as this package does, keeps night shifts and cross-midnight
sessions correct as well.

## 6. The summary is rebuilt by hand

`Summary (2)` is a set of thirteen side-by-side blocks (`EARLY IN`, `LATE IN`,
`LATE OUT`, `EARLY OUT`, `WORKING DURING OFF`, `< 1 Hour Break`,
`Missed Time IN/OUT`, `OUTSIDE WORK`, `BUSINESS TRIP`, `OFF`, `SICK LEAVE`,
`ON LEAVE`), populated by copying names across from the daily sheet. Names in it
are abbreviated inconsistently (`Vasanth` vs `Vasanth Karuppaiya`, `Nagendra` vs
`Nagendra Prasad`), so the blocks cannot be reconciled with the daily sheet
automatically.

`matrixreports summary` derives every block from the same day records the daily
report uses, so the two cannot disagree, and emits them stacked rather than side
by side so each is a real filterable table.

## Thresholds inferred from the sheets

These are the defaults in `config/matrixreports.example.yaml`; confirm them
before the first run.

| Rule | Value | Source |
| --- | --- | --- |
| Shift | 10:00 – 19:00 | `Sheet (2)!A5`, `Sheet!C12` (`10:00- 19:00`) |
| Late IN after | 10:10 | `Summary (2)!E3` (`LATE IN ( AFTER 10:10 AM)`) |
| Late OUT after | 19:10 | `Summary (2)!AC3` (`LATE OUT (AFTER 07:10PM)`) |
| Early IN before | 10:00 | `Summary (2)!A3` |
| Long break alert | 1 hour | `Summary (2)!AP3` |
| Weekly off | Friday | `OFF` cells in `Monthly_Attendance_2026_.xlsx!June` |
| Leave codes | AL, SL, CL, EL, UL, CO, WFH, BT, OW | `Jan Sum!AI5:AT5` |
