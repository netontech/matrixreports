"""The pairing engine: unlimited in/outs, and sane handling of messy punches."""

from datetime import date, datetime, time, timedelta

from matrixreports.duration import hhmm
from matrixreports.model import Anomaly, Direction, Employee, Punch
from matrixreports.pairing import PairingPolicy, attendance_day, build_day_record

from conftest import DAY, HEAVY_DAY, at, punches

EMPLOYEE = Employee("1", "Heavy Walker")


def build(clocks, **kwargs):
    policy = kwargs.pop("policy", PairingPolicy())
    return build_day_record(
        EMPLOYEE, DAY, punches("1", clocks),
        policy=policy, shift_start_time=time(10, 0), shift_end_time=time(19, 0),
        **kwargs,
    )


def test_more_than_six_in_outs_are_all_kept():
    """The whole point: 24 punches produce 12 sessions and 11 breaks, not 6."""
    record = build(HEAVY_DAY)
    assert len(record.punches) == 24
    assert len(record.sessions) == 12
    assert record.break_count == 11
    # Every break is a real interval with a positive duration.
    assert all(brk.duration > timedelta(0) for brk in record.breaks)


def test_worked_plus_breaks_equals_span():
    """The invariant the legacy sheet violates once it drops the 6th break."""
    record = build(HEAVY_DAY)
    assert record.worked + record.break_total == record.span
    assert record.first_in == at("09:44")
    assert record.last_out == at("19:09")


def test_dropping_breaks_past_the_fifth_overstates_worked_time():
    """Quantifies the reporting error the client is hitting."""
    record = build(HEAVY_DAY)
    truncated_breaks = sum(
        (brk.duration for brk in record.breaks[:5]), timedelta(0)
    )
    assert truncated_breaks < record.break_total
    lost = record.break_total - truncated_breaks
    assert lost > timedelta(0)
    assert hhmm(lost) != "00:00"


def test_duplicate_reads_are_collapsed():
    """A badge read twice at the same door registers as one punch."""
    raw = [
        Punch("1", at("10:00"), Direction.IN),
        Punch("1", at("10:00"), Direction.IN),      # controller double-read
        Punch("1", at("13:00"), Direction.OUT),
        Punch("1", at("13:30"), Direction.IN),
        Punch("1", at("19:00"), Direction.OUT),
        Punch("1", at("19:00"), Direction.OUT),     # and again on the way out
    ]
    record = build_day_record(
        EMPLOYEE, DAY, raw, shift_start_time=time(10, 0), shift_end_time=time(19, 0)
    )
    assert Anomaly.DUPLICATE_PUNCH in record.anomalies
    assert len(record.sessions) == 2
    assert record.break_count == 1
    assert hhmm(record.worked) == "08:30"


def test_direction_is_inferred_when_the_device_does_not_record_it():
    raw = [Punch("1", at(clock)) for clock in ["10:00", "13:00", "13:30", "19:00"]]
    record = build_day_record(
        EMPLOYEE, DAY, raw,
        policy=PairingPolicy(direction_mode="alternate"),
        shift_start_time=time(10, 0), shift_end_time=time(19, 0),
    )
    assert Anomaly.DIRECTION_INFERRED in record.anomalies
    assert record.break_count == 1
    assert hhmm(record.worked) == "08:30"


def test_consecutive_ins_keep_the_earliest():
    raw = [
        Punch("1", at("10:00"), Direction.IN),
        Punch("1", at("10:40"), Direction.IN),
        Punch("1", at("19:00"), Direction.OUT),
    ]
    record = build_day_record(
        EMPLOYEE, DAY, raw, shift_start_time=time(10, 0), shift_end_time=time(19, 0)
    )
    assert Anomaly.CONSECUTIVE_IN in record.anomalies
    assert record.first_in == at("10:00")
    assert hhmm(record.worked) == "09:00"


def test_consecutive_outs_keep_the_latest():
    raw = [
        Punch("1", at("10:00"), Direction.IN),
        Punch("1", at("18:00"), Direction.OUT),
        Punch("1", at("19:00"), Direction.OUT),
    ]
    record = build_day_record(
        EMPLOYEE, DAY, raw, shift_start_time=time(10, 0), shift_end_time=time(19, 0)
    )
    assert Anomaly.CONSECUTIVE_OUT in record.anomalies
    assert record.last_out == at("19:00")


def test_missing_final_out_is_closed_at_shift_end_and_flagged():
    record = build(["10:00", "13:00", "13:30"])
    assert Anomaly.MISSING_OUT in record.anomalies
    assert Anomaly.OPEN_SESSION_CLOSED in record.anomalies
    assert record.last_out == at("19:00")


def test_missing_final_out_can_be_dropped_instead():
    record = build(["10:00", "13:00", "13:30"],
                   policy=PairingPolicy(open_session_policy="drop"))
    assert Anomaly.MISSING_OUT in record.anomalies
    assert record.last_out == at("13:00")


def test_leading_out_without_an_in_is_flagged_not_counted():
    raw = [
        Punch("1", at("09:00"), Direction.OUT),
        Punch("1", at("10:00"), Direction.IN),
        Punch("1", at("19:00"), Direction.OUT),
    ]
    record = build_day_record(
        EMPLOYEE, DAY, raw, shift_start_time=time(10, 0), shift_end_time=time(19, 0)
    )
    assert Anomaly.MISSING_IN in record.anomalies
    assert hhmm(record.worked) == "09:00"


def test_no_punches_is_flagged_and_has_no_metrics():
    record = build([])
    assert Anomaly.NO_PUNCHES in record.anomalies
    assert record.worked is None
    assert record.first_in is None
    assert record.break_count == 0


def test_lateness_metrics():
    record = build(["10:25", "19:40"])
    assert hhmm(record.late_in) == "00:25"
    assert hhmm(record.late_out) == "00:40"
    assert hhmm(record.early_out) == "00:00"


def test_early_out_metric():
    record = build(["09:50", "16:30"])
    assert hhmm(record.early_out) == "02:30"
    assert hhmm(record.late_in) == "00:00"


def test_short_breaks_can_be_merged_away():
    clocks = ["10:00", "12:00", "12:01", "19:00"]
    assert build(clocks).break_count == 1
    merged = build(clocks, policy=PairingPolicy(merge_breaks_under_seconds=120))
    assert merged.break_count == 0
    assert hhmm(merged.worked) == "09:00"


def test_night_shift_day_boundary():
    policy = PairingPolicy(day_start_hour=4)
    assert attendance_day(datetime(2026, 6, 2, 1, 30), policy) == date(2026, 6, 1)
    assert attendance_day(datetime(2026, 6, 2, 5, 30), policy) == date(2026, 6, 2)


def test_night_shift_session_crosses_midnight():
    raw = [
        Punch("1", datetime(2026, 6, 1, 22, 0), Direction.IN),
        Punch("1", datetime(2026, 6, 2, 6, 0), Direction.OUT),
    ]
    record = build_day_record(
        EMPLOYEE, DAY, raw,
        policy=PairingPolicy(day_start_hour=4),
        shift_start_time=time(22, 0), shift_end_time=time(6, 0),
    )
    assert hhmm(record.worked) == "08:00"
    assert record.shift_end.date() == date(2026, 6, 2)
