"""A small table model shared by every report.

Reports build a :class:`ReportTable`; the exporters render it.  Keeping the two
apart is what lets the daily report grow an arbitrary number of OUT/IN groups
without any exporter needing to know how many there will be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class Cell:
    """One value plus the *kind* of value it is.

    ``style`` drives the number format at export time.  Durations are written as
    real Excel times with a ``[h]:mm`` format, so they display as ``08:14`` and
    still add up correctly inside the workbook — unlike the legacy ``8.14``.
    """

    value: Any = None
    style: str = "text"     # text|time|duration|number|int|percent|date|code
    note: str = ""

    @property
    def is_blank(self) -> bool:
        return self.value is None or self.value == ""


@dataclass(frozen=True, slots=True)
class Column:
    header: str
    style: str = "text"
    width: float = 12.0
    group: str = ""          # label of the banded group header above, if any


@dataclass(slots=True)
class ReportTable:
    """A rendered report: titles, columns, rows, and free-form metadata."""

    key: str
    title: str
    subtitle: str = ""
    company: str = ""
    columns: list[Column] = field(default_factory=list)
    rows: list[list[Cell]] = field(default_factory=list)
    group_spans: list[tuple[str, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def add_row(self, cells: list[Cell]) -> None:
        if len(cells) != len(self.columns):
            raise ValueError(
                f"row has {len(cells)} cells but the table has {len(self.columns)} columns"
            )
        self.rows.append(cells)


def text(value: Any) -> Cell:
    return Cell("" if value is None else str(value), "text")


def code(value: Any) -> Cell:
    return Cell("" if value is None else str(value), "code")


def integer(value: int | None) -> Cell:
    return Cell(value, "int")


def number(value: float | None) -> Cell:
    return Cell(value, "number")


def percent(value: float | None) -> Cell:
    return Cell(value, "percent")


def clock(value: datetime | None) -> Cell:
    """A wall-clock time, e.g. the moment someone punched in."""
    return Cell(value, "time")


def duration(value: timedelta | None) -> Cell:
    """An elapsed span, e.g. hours worked. Never a decimal."""
    return Cell(value, "duration")


def day(value: date | None) -> Cell:
    return Cell(value, "date")


def group_columns(count: int) -> list[Column]:
    """The repeating ``OUT / IN / MINS`` block, ``count`` times over.

    This function is the whole of the "N in/outs" change as far as layout goes:
    the old writer called the equivalent with a literal 5.
    """
    columns: list[Column] = []
    for index in range(1, count + 1):
        label = f"Break {index}"
        columns.append(Column("OUT", "time", 9.5, label))
        columns.append(Column("IN", "time", 9.5, label))
        columns.append(Column("MINS", "duration", 8.0, label))
    return columns
