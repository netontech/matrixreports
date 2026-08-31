"""Exact duration handling.

The legacy Matrix workbooks store durations as ``hh.mm`` *decimal numbers*
(``8.14`` meaning 8 hours 14 minutes) and then run ``SUM()`` over them.  That is
arithmetically wrong: ``8.14 + 8.50`` yields ``16.64`` where the true total is
``16:64`` -> ``17:04``.  Every monthly and weekly total in the supplied reports is
skewed by this.  Nothing in this package ever represents a duration as a decimal
number of that kind; durations are :class:`datetime.timedelta` end to end and are
only formatted at the moment they are written out.
"""

from __future__ import annotations

import re
from datetime import timedelta

ZERO = timedelta(0)

_HHMM_RE = re.compile(r"^\s*(-)?(\d+):([0-5]?\d)(?::([0-5]?\d))?\s*$")


def hhmm(value: timedelta | None, *, blank: str = "") -> str:
    """Format a duration as ``HH:MM``, rolling hours past 24 rather than wrapping.

    ``None`` renders as *blank* so empty cells stay empty instead of showing
    ``00:00`` (the legacy sheets conflate "no data" with "zero", which is why a
    day on sick leave shows the same ``00:00`` as a day someone forgot to punch).
    """
    if value is None:
        return blank
    total = int(round(value.total_seconds() / 60))
    sign = "-" if total < 0 else ""
    total = abs(total)
    return f"{sign}{total // 60:02d}:{total % 60:02d}"


def hhmmss(value: timedelta | None, *, blank: str = "") -> str:
    if value is None:
        return blank
    total = int(round(value.total_seconds()))
    sign = "-" if total < 0 else ""
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_hhmm(text: str) -> timedelta | None:
    """Parse ``HH:MM`` / ``HH:MM:SS`` back into a duration.

    Used when reading the legacy workbooks, which mix ``'54:00'`` strings and
    ``54.0`` numbers in the same column.
    """
    if text is None:
        return None
    match = _HHMM_RE.match(str(text))
    if not match:
        return None
    neg, hours, minutes, seconds = match.groups()
    value = timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds or 0))
    return -value if neg else value


def parse_legacy_decimal(value: float) -> timedelta:
    """Interpret a legacy ``hh.mm`` decimal (``8.14`` -> 8h14m).

    Only for reading historic sheets.  Never use it to produce new figures.
    """
    hours = int(value)
    minutes = int(round((value - hours) * 100))
    return timedelta(hours=hours, minutes=minutes)


def excel_time(value: timedelta | None) -> float | None:
    """Convert to an Excel serial time (a fraction of a day).

    Written with a ``[h]:mm`` number format so the cell both *displays* as
    ``HH:MM`` and stays summable inside Excel — the legacy sheets get to pick one
    or the other, not both.
    """
    if value is None:
        return None
    return value.total_seconds() / 86400.0


def total_minutes(value: timedelta | None) -> int | None:
    if value is None:
        return None
    return int(round(value.total_seconds() / 60))


def summed(values) -> timedelta:
    total = ZERO
    for value in values:
        if value is not None:
            total += value
    return total
