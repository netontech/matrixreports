"""Attendance reporting for Matrix (COSEC) access-control installations.

The Matrix reporting module flattens each attendance day into six fixed
``IN``/``OUT`` slots, so any employee who steps out more than five times has the
remainder of their movement silently dropped from the report — even though every
punch is present in the underlying database.  This package reads those raw
punches and rebuilds the daily, weekly, monthly and yearly reports with no cap on
the number of in/out pairs per day.
"""

from .builder import AttendanceBook, AttendanceBuilder
from .config import Config
from .datasource import CsvPunchSource, InMemoryPunchSource, SqlPunchSource, open_source
from .duration import hhmm, parse_hhmm
from .model import Anomaly, DayRecord, Direction, Employee, Interval, Punch
from .pairing import PairingPolicy, build_day_record

__version__ = "0.1.0"

__all__ = [
    "Anomaly",
    "AttendanceBook",
    "AttendanceBuilder",
    "Config",
    "CsvPunchSource",
    "DayRecord",
    "Direction",
    "Employee",
    "InMemoryPunchSource",
    "Interval",
    "PairingPolicy",
    "Punch",
    "SqlPunchSource",
    "__version__",
    "build_day_record",
    "hhmm",
    "open_source",
    "parse_hhmm",
]
