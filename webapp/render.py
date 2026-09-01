"""Turn a ReportTable into HTML.

The exporters in ``matrixreports.excel`` and this renderer read the *same*
ReportTable, so the browser view and the workbook cannot drift apart.  Nothing
here knows how many OUT/IN groups there are: the group band is driven by
``table.group_spans`` and the columns by ``table.columns``, both sized to the
data by the report builder.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from matrixreports.duration import hhmm

# Mirrors STATUS_FILL in excel.py so the two views colour a day the same way.
STATUS_CLASS = {
    "OFF": "s-off", "HOL": "s-hol", "A": "s-absent", "Absent": "s-absent",
    "WO": "s-off", "Sick Leave": "s-leave", "Annual Leave": "s-leave",
}


def cell_text(cell) -> str:
    value = cell.value
    if value is None or value == "":
        return ""
    if cell.style == "duration":
        return hhmm(value) if isinstance(value, timedelta) else str(value)
    if cell.style == "time":
        if isinstance(value, datetime):
            return value.strftime("%H:%M")
        if isinstance(value, timedelta):
            return hhmm(value)
        return str(value)
    if cell.style == "date":
        return value.strftime("%d-%b-%Y") if isinstance(value, (date, datetime)) else str(value)
    if cell.style == "percent":
        try:
            return f"{float(value) * 100:.0f}%"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def cell_class(cell) -> str:
    classes = []
    if cell.style in {"duration", "time", "int", "number", "percent"}:
        classes.append("num")
    if cell.style == "code":
        classes.append("code")
    if isinstance(cell.value, str) and cell.value in STATUS_CLASS:
        classes.append(STATUS_CLASS[cell.value])
    return " ".join(classes)


def render_rows(table):
    """Rows as (cells, row_class) so the template stays free of logic."""
    out = []
    for row in table.rows:
        issues = ""
        for column, cell in zip(table.columns, row):
            if column.header == "Data Issues" and cell.value:
                issues = str(cell.value)
        out.append((row, "has-issues" if issues else ""))
    return out


def band(table):
    """The merged group header row, e.g. 1 | 2 | 3 ... over OUT/IN/MINS.

    Returns None when a report has no groups, so the template can skip the row
    rather than emit an empty one.
    """
    return table.group_spans or None
