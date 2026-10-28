"""
Tests for the shared, thread-safe AttendanceEngine (ENTRY/EXIT state machine).
"""
import threading
import time
from datetime import datetime, timedelta

import pytest

from app.services import AttendanceEngine, get_shared_attendance_engine

STUDENT = 7
IN_CAM = 1
OUT_CAM = 2


def local_ts(hour: int, minute: int = 0, second: float = 0, days_ago: int = 0) -> float:
    """Epoch seconds for a local wall-clock time today (or days_ago)"""
    day = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=days_ago)
    return day.timestamp() + second


def make_engine(**overrides) -> AttendanceEngine:
    params = dict(confidence_threshold=70.0, min_consecutive_frames=3, entry_cooldown=300.0,
                  exit_cooldown=60.0, min_presence_seconds=60.0, max_frame_gap=2.0)
    params.update(overrides)
    return AttendanceEngine(**params)


def see(engine, start, camera_type="IN", camera_id=IN_CAM, frames=3, student=STUDENT,
        confidence=90.0, step=0.1, persist=None):
    """Feed `frames` consecutive sightings; return all decisions"""
    return [engine.process_recognition(student, confidence, start + i * step, camera_type,
                                       camera_id=camera_id, persist=persist)
            for i in range(frames)]


def recorded(decisions):
    return [d for d in decisions if d.should_record]


# --- ENTRY -----------------------------------------------------------------

def test_first_stable_sighting_records_entry():
    """Regression: the old engine needed ~300 's of frames' before the first ENTRY"""
    engine = make_engine()
    decisions = see(engine, local_ts(9))
    assert [d.event_type for d in decisions] == ["STABILIZING", "STABILIZING", "ENTRY"]
    assert decisions[-1].should_record
    assert decisions[-1].camera_id == IN_CAM
    assert engine.inside_count(local_ts(9, 1)) == 1


def test_no_duplicate_entry_while_inside_even_after_cooldown():
    engine = make_engine()
    assert len(recorded(see(engine, local_ts(9)))) == 1
    # Student lingers in front of the IN camera an hour later: still inside, no new ENTRY
    later = see(engine, local_ts(10))
    assert recorded(later) == []
    assert "already inside" in later[-1].reasoning


def test_low_confidence_never_records_and_resets_run():
    engine = make_engine()
    t = local_ts(9)
    engine.process_recognition(STUDENT, 90, t, "IN", camera_id=IN_CAM)
    engine.process_recognition(STUDENT, 90, t + 0.1, "IN", camera_id=IN_CAM)
    low = engine.process_recognition(STUDENT, 69.9, t + 0.2, "IN", camera_id=IN_CAM)
    assert low.event_type == "LOW_CONFIDENCE" and not low.should_record
    after = engine.process_recognition(STUDENT, 90, t + 0.3, "IN", camera_id=IN_CAM)
    assert after.event_type == "STABILIZING"  # run restarted


def test_gap_in_sightings_resets_consecutive_count():
    engine = make_engine()
    t = local_ts(9)
    see(engine, t, frames=2)
    # 5s later (> max_frame_gap): the run starts over, so one more frame is not enough
    d = engine.process_recognition(STUDENT, 90, t + 5, "IN", camera_id=IN_CAM)
    assert d.event_type == "STABILIZING"


def test_consecutive_frames_are_counted_per_camera():
    engine = make_engine()
    t = local_ts(9)
    decisions = []
    for i in range(4):  # alternate two IN cameras: neither reaches 3 consecutive
        cam = 1 if i % 2 == 0 else 3
        decisions.append(engine.process_recognition(STUDENT, 90, t + i * 0.1, "IN", camera_id=cam))
    assert recorded(decisions) == []


def test_unknown_person_never_records():
    engine = make_engine()
    decisions = [engine.process_recognition(None, 0.0, local_ts(9) + i, "IN", camera_id=IN_CAM) for i in range(10)]
    assert all(d.event_type == "UNKNOWN" and not d.should_record for d in decisions)


# --- EXIT ------------------------------------------------------------------

def test_out_camera_without_active_entry_never_exits():
    engine = make_engine()
    decisions = see(engine, local_ts(9), camera_type="OUT", camera_id=OUT_CAM, frames=10)
    assert recorded(decisions) == []
    assert "no active entry" in decisions[-1].reasoning


def test_entry_then_exit_across_two_cameras():
    engine = make_engine()
    entry = recorded(see(engine, local_ts(9), "IN", IN_CAM))
    too_soon = see(engine, local_ts(9, 0, 30), "OUT", OUT_CAM)
    assert recorded(too_soon) == []
    assert "min 60.0s" in too_soon[-1].reasoning
    exit_ = recorded(see(engine, local_ts(10), "OUT", OUT_CAM))
    assert [d.event_type for d in entry + exit_] == ["ENTRY", "EXIT"]
    assert exit_[0].camera_id == OUT_CAM
    assert engine.inside_count(local_ts(10, 1)) == 0


def test_no_duplicate_exit():
    engine = make_engine()
    see(engine, local_ts(9), "IN", IN_CAM)
    assert len(recorded(see(engine, local_ts(10), "OUT", OUT_CAM))) == 1
    assert recorded(see(engine, local_ts(10, 30), "OUT", OUT_CAM)) == []


def test_entry_from_previous_day_is_not_active():
    engine = make_engine()
    see(engine, local_ts(9, days_ago=1), "IN", IN_CAM)  # never exited yesterday
    # Today: OUT camera must not close yesterday's entry...
    assert recorded(see(engine, local_ts(8), "OUT", OUT_CAM)) == []
    # ...and the IN camera records today's ENTRY
    today = recorded(see(engine, local_ts(8, 5), "IN", IN_CAM))
    assert [d.event_type for d in today] == ["ENTRY"]


def test_reentry_respects_exit_and_entry_cooldowns():
    engine = make_engine(entry_cooldown=300.0, exit_cooldown=60.0, min_presence_seconds=10.0)
    see(engine, local_ts(9), "IN", IN_CAM)
    see(engine, local_ts(9, 1), "OUT", OUT_CAM)  # EXIT 60s after entry
    # 30s after exit: blocked by exit cooldown (and entry cooldown)
    assert recorded(see(engine, local_ts(9, 1, 30), "IN", IN_CAM)) == []
    # 3 min after entry, 2 min after exit: exit cooldown passed, entry cooldown not
    blocked = see(engine, local_ts(9, 3), "IN", IN_CAM)
    assert recorded(blocked) == [] and "since last entry" in blocked[-1].reasoning
    # 10 min later: allowed
    assert [d.event_type for d in recorded(see(engine, local_ts(9, 10), "IN", IN_CAM))] == ["ENTRY"]


# --- persistence and restart ------------------------------------------------

def test_persist_failure_rolls_back_state():
    engine = make_engine()
    calls = []

    def failing(decision):
        calls.append(decision)
        raise RuntimeError("db down")

    decisions = see(engine, local_ts(9), persist=failing)
    assert len(calls) == 1
    assert not decisions[-1].should_record
    assert "Persistence failed" in decisions[-1].reasoning
    assert engine.inside_count(local_ts(9, 1)) == 0
    # Once the DB recovers, the next stable run records the ENTRY
    saved = []
    assert len(recorded(see(engine, local_ts(9, 0, 1), persist=saved.append))) == 1
    assert len(saved) == 1


def test_seed_from_events_restores_inside_state():
    engine = make_engine()
    engine.seed_from_events([(STUDENT, "ENTRY", local_ts(8))])
    assert recorded(see(engine, local_ts(9), "IN", IN_CAM)) == []  # no duplicate after restart
    assert [d.event_type for d in recorded(see(engine, local_ts(10), "OUT", OUT_CAM))] == ["EXIT"]


def test_seed_ignores_events_older_than_known_state():
    engine = make_engine()
    see(engine, local_ts(9), "IN", IN_CAM)
    see(engine, local_ts(10), "OUT", OUT_CAM)
    engine.seed_from_events([(STUDENT, "ENTRY", local_ts(9))])  # stale
    assert engine.inside_count(local_ts(10, 1)) == 0


def test_cleanup_keeps_students_who_are_inside():
    engine = make_engine()
    see(engine, local_ts(8), "IN", IN_CAM)
    engine.last_cleanup = 0
    # Another student seen 3 hours later triggers cleanup
    see(engine, local_ts(11), "IN", IN_CAM, student=99)
    assert STUDENT in engine.student_states
    assert recorded(see(engine, local_ts(11, 5), "IN", IN_CAM)) == []


def test_shared_engine_is_a_singleton():
    a = get_shared_attendance_engine(min_consecutive_frames=3)
    b = get_shared_attendance_engine(min_consecutive_frames=99)
    assert a is b and a.min_consecutive_frames == 3


# --- concurrency -------------------------------------------------------------

@pytest.mark.parametrize("cameras", [[("IN", 1), ("IN", 3), ("IN", 4), ("IN", 5)]])
def test_simultaneous_in_cameras_record_one_entry(cameras):
    engine = make_engine(min_consecutive_frames=2)
    persisted = []
    persist_lock = threading.Lock()

    def slow_persist(decision):
        time.sleep(0.01)  # widen the race window
        with persist_lock:
            persisted.append(decision)

    start = threading.Barrier(len(cameras))
    t0 = local_ts(9)

    def worker(camera_type, camera_id):
        start.wait()
        for i in range(20):
            engine.process_recognition(STUDENT, 95, t0 + i * 0.1, camera_type, camera_id=camera_id, persist=slow_persist)

    threads = [threading.Thread(target=worker, args=c) for c in cameras]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert [d.event_type for d in persisted] == ["ENTRY"]


def test_simultaneous_in_and_out_cameras_stay_consistent():
    """IN and OUT cameras race on many students; every student's persisted
    history must be a valid alternation starting with ENTRY."""
    engine = make_engine(min_consecutive_frames=1, min_presence_seconds=0.0, exit_cooldown=0.0, entry_cooldown=0.0)
    persisted = []
    lock = threading.Lock()

    def persist(decision):
        with lock:
            persisted.append((decision.student_id, decision.event_type))

    t0 = local_ts(9)

    def worker(camera_type, camera_id):
        for i in range(200):
            engine.process_recognition(i % 5, 95, t0 + i * 0.01, camera_type, camera_id=camera_id, persist=persist)

    threads = [threading.Thread(target=worker, args=a) for a in [("IN", 1), ("OUT", 2), ("IN", 3), ("OUT", 4)]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for student in range(5):
        history = [e for s, e in persisted if s == student]
        assert history, f"student {student} never recorded"
        assert history[0] == "ENTRY"
        for prev, nxt in zip(history, history[1:]):
            assert prev != nxt, f"student {student} has duplicate {prev}: {history}"
