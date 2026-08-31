"""Turn raw punches into an unbounded sequence of sessions and breaks.

This is the module that lifts the six-in/out limit.  The Matrix report writer
pairs punches into a fixed set of six ``OUT``/``IN`` slots; anything beyond the
fifth break is discarded before it ever reaches the sheet, which is why an
employee who steps out seven times shows the same "No. Of OUT = 5" as one who
stepped out five.  Here the pairing is a plain fold over the punch list, so the
number of sessions is whatever the data says it is.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .model import Anomaly, DayRecord, Direction, Employee, Interval, Punch


class PairingPolicy:
    """Tunable rules for reconciling imperfect punch data.

    Defaults are chosen to match how the client's floor actually behaves: door
    controllers double-read, people occasionally miss the reader on the way out,
    and a handful of staff work past midnight.
    """

    def __init__(
        self,
        *,
        dedup_seconds: int = 60,
        direction_mode: str = "device",       # "device" | "alternate"
        open_session_policy: str = "shift_end",  # "shift_end"|"last_punch"|"leave_open"|"drop"
        merge_breaks_under_seconds: int = 0,
        day_start_hour: int = 0,
        max_session_hours: int = 18,
    ) -> None:
        if direction_mode not in {"device", "alternate"}:
            raise ValueError(f"unknown direction_mode: {direction_mode!r}")
        if open_session_policy not in {"shift_end", "last_punch", "leave_open", "drop"}:
            raise ValueError(f"unknown open_session_policy: {open_session_policy!r}")
        self.dedup_seconds = dedup_seconds
        self.direction_mode = direction_mode
        self.open_session_policy = open_session_policy
        self.merge_breaks_under_seconds = merge_breaks_under_seconds
        self.day_start_hour = day_start_hour
        self.max_session_hours = max_session_hours


def attendance_day(moment: datetime, policy: PairingPolicy) -> date:
    """Which attendance day a punch belongs to.

    With ``day_start_hour = 4`` a punch at 01:30 counts against the previous
    day, so a night shift stays on one row instead of being split in two.
    """
    if policy.day_start_hour and moment.hour < policy.day_start_hour:
        return (moment - timedelta(days=1)).date()
    return moment.date()


def dedupe(punches: list[Punch], policy: PairingPolicy) -> tuple[list[Punch], bool]:
    """Drop repeat reads of the same direction inside the dedup window."""
    if policy.dedup_seconds <= 0 or not punches:
        return list(punches), False
    window = timedelta(seconds=policy.dedup_seconds)
    kept: list[Punch] = [punches[0]]
    dropped = False
    for punch in punches[1:]:
        previous = kept[-1]
        same_direction = (
            punch.direction == previous.direction
            or Direction.UNKNOWN in (punch.direction, previous.direction)
        )
        if same_direction and punch.timestamp - previous.timestamp <= window:
            dropped = True
            continue
        kept.append(punch)
    return kept, dropped


def resolve_directions(
    punches: list[Punch], policy: PairingPolicy
) -> tuple[list[Punch], bool]:
    """Fill in missing IN/OUT flags by strict alternation.

    Many Matrix installations record only a timestamp and a door, leaving the
    direction to be inferred.  We alternate from the last known direction, or
    from ``IN`` when nothing is known — the first read of a day is an entry.
    """
    inferred = False
    resolved: list[Punch] = []
    previous: Direction | None = None
    for punch in punches:
        direction = punch.direction
        if policy.direction_mode == "alternate" or direction == Direction.UNKNOWN:
            if previous is None:
                direction = Direction.IN
            else:
                direction = Direction.OUT if previous == Direction.IN else Direction.IN
            if direction != punch.direction:
                inferred = True
            punch = Punch(punch.emp_id, punch.timestamp, direction, punch.device, punch.source)
        resolved.append(punch)
        previous = direction
    return resolved, inferred


def collapse_repeats(punches: list[Punch]) -> tuple[list[Punch], list[Anomaly]]:
    """Reduce runs of same-direction punches to a single meaningful one.

    For a run of ``IN``s the earliest is the real entry; for a run of ``OUT``s
    the latest is the real exit.  Keeping the outermost punch of each run is the
    conservative choice: it never invents presence the data does not support.
    """
    anomalies: list[Anomaly] = []
    kept: list[Punch] = []
    for punch in punches:
        if kept and kept[-1].direction == punch.direction:
            if punch.direction == Direction.IN:
                anomalies.append(Anomaly.CONSECUTIVE_IN)
                continue                      # keep the earliest IN
            anomalies.append(Anomaly.CONSECUTIVE_OUT)
            kept[-1] = punch                  # keep the latest OUT
            continue
        kept.append(punch)
    return kept, anomalies


def build_sessions(
    punches: list[Punch],
    policy: PairingPolicy,
    shift_end: datetime | None,
) -> tuple[list[Interval], list[Anomaly]]:
    """Fold the punch list into sessions. No cap on how many are produced."""
    sessions: list[Interval] = []
    anomalies: list[Anomaly] = []
    open_start: datetime | None = None

    for punch in punches:
        if punch.direction == Direction.IN:
            open_start = punch.timestamp
        else:
            if open_start is None:
                # An OUT with nothing open: the matching entry was never recorded.
                anomalies.append(Anomaly.MISSING_IN)
                continue
            sessions.append(Interval(open_start, punch.timestamp))
            open_start = None

    if open_start is not None:
        anomalies.append(Anomaly.MISSING_OUT)
        if policy.open_session_policy == "shift_end" and shift_end and shift_end > open_start:
            sessions.append(Interval(open_start, shift_end))
            anomalies.append(Anomaly.OPEN_SESSION_CLOSED)
        elif policy.open_session_policy == "leave_open":
            sessions.append(Interval(open_start, None))
        elif policy.open_session_policy == "last_punch":
            # Zero-length: the employee is credited only up to their final read.
            sessions.append(Interval(open_start, open_start))
            anomalies.append(Anomaly.OPEN_SESSION_CLOSED)
        # "drop" falls through, leaving the dangling IN out of the totals.

    limit = timedelta(hours=policy.max_session_hours)
    for session in sessions:
        if session.duration is not None and session.duration > limit:
            anomalies.append(Anomaly.CROSSES_MIDNIGHT)
            break
    return sessions, anomalies


def merge_short_breaks(sessions: list[Interval], policy: PairingPolicy) -> list[Interval]:
    """Optionally treat sub-threshold gaps as continued presence.

    Off by default.  Turning it on (say 120s) stops a door re-read from being
    reported as a break, without hiding a genuine step-out.
    """
    if policy.merge_breaks_under_seconds <= 0 or len(sessions) < 2:
        return sessions
    threshold = timedelta(seconds=policy.merge_breaks_under_seconds)
    merged: list[Interval] = [sessions[0]]
    for session in sessions[1:]:
        previous = merged[-1]
        if previous.end is not None and session.start - previous.end < threshold:
            merged[-1] = Interval(previous.start, session.end)
        else:
            merged.append(session)
    return merged


def derive_breaks(sessions: list[Interval]) -> list[Interval]:
    """The gaps between consecutive sessions — the report's OUT/IN pairs."""
    breaks: list[Interval] = []
    for previous, following in zip(sessions, sessions[1:]):
        if previous.end is not None:
            breaks.append(Interval(previous.end, following.start))
    return breaks


def shift_bounds(
    day: date, start: time | None, end: time | None
) -> tuple[datetime | None, datetime | None]:
    """Materialise a shift's start/end on a given day, handling night shifts."""
    if start is None or end is None:
        return None, None
    shift_start = datetime.combine(day, start)
    shift_end = datetime.combine(day, end)
    if end <= start:                       # e.g. 22:00 -> 06:00
        shift_end += timedelta(days=1)
    return shift_start, shift_end


def build_day_record(
    employee: Employee,
    day: date,
    punches: list[Punch],
    *,
    policy: PairingPolicy | None = None,
    shift_start_time: time | None = None,
    shift_end_time: time | None = None,
    status: str = "",
    is_working_day: bool = True,
) -> DayRecord:
    """Build a complete :class:`DayRecord` from one day's raw punches."""
    policy = policy or PairingPolicy()
    shift_start, shift_end = shift_bounds(day, shift_start_time, shift_end_time)
    record = DayRecord(
        employee=employee,
        day=day,
        punches=sorted(punches, key=lambda p: p.timestamp),
        status=status,
        is_working_day=is_working_day,
        shift_start=shift_start,
        shift_end=shift_end,
    )
    if not record.punches:
        record.anomalies.append(Anomaly.NO_PUNCHES)
        return record

    ordered, dropped = dedupe(record.punches, policy)
    if dropped:
        record.anomalies.append(Anomaly.DUPLICATE_PUNCH)

    ordered, inferred = resolve_directions(ordered, policy)
    if inferred:
        record.anomalies.append(Anomaly.DIRECTION_INFERRED)

    ordered, repeat_anomalies = collapse_repeats(ordered)
    record.anomalies.extend(dict.fromkeys(repeat_anomalies))

    sessions, session_anomalies = build_sessions(ordered, policy, shift_end)
    sessions = merge_short_breaks(sessions, policy)
    record.sessions = sessions
    record.breaks = derive_breaks(sessions)
    for anomaly in session_anomalies:
        if anomaly not in record.anomalies:
            record.anomalies.append(anomaly)
    return record
