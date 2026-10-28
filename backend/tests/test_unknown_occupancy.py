"""
UnknownOccupancyTracker: estimated unregistered people inside, matched by face encoding.

Pure unit tests with synthetic 128-d encodings and fixed timestamps - no database,
camera, network or model download.
"""
import sys
import threading
from datetime import datetime, timedelta

import numpy as np
import pytest

from app.config import Settings
from app.services import (FaceObservationKind, UnknownOccupancyTracker, get_shared_unknown_tracker,
                          reset_shared_unknown_tracker)

UNKNOWN = FaceObservationKind.UNKNOWN
AMBIGUOUS = FaceObservationKind.AMBIGUOUS
KNOWN = FaceObservationKind.KNOWN
RNG = np.random.default_rng(2026)


def local_ts(hour, minute=0, second=0.0, days_ago=0):
    day = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=days_ago)
    return day.timestamp() + second


def random_encoding():
    # Random encodings at this scale are ~1.4 apart: clearly different people
    return RNG.normal(0, 0.09, 128)


def at_distance(base, distance):
    direction = RNG.normal(size=128)
    return base + direction / np.linalg.norm(direction) * distance


def make_tracker(**overrides):
    params = dict(same_person_distance=0.6, min_consecutive_frames=3, max_frame_gap=2.0,
                  exit_cooldown=60.0, min_presence_seconds=60.0, now=local_ts(8))
    params.update(overrides)
    return UnknownOccupancyTracker(**params)


def see(tracker, encoding, start, camera_type="IN", camera_key="in-1", frames=3, step=0.1,
        kind=UNKNOWN, jitter=0.0):
    """Feed `frames` consecutive sightings of one face; returns the results"""
    return [tracker.observe(kind, at_distance(encoding, jitter) if jitter else encoding,
                            start + i * step, camera_type, camera_key)
            for i in range(frames)]


def events(results):
    return [r.event for r in results]


@pytest.fixture(autouse=True)
def fresh_shared_tracker():
    reset_shared_unknown_tracker()
    yield
    reset_shared_unknown_tracker()


# --- entry ----------------------------------------------------------------------------

def test_no_count_before_required_consecutive_sightings():
    tracker = make_tracker()
    results = see(tracker, random_encoding(), local_ts(9), frames=2)
    assert events(results) == ["CANDIDATE", "CANDIDATE"]
    assert tracker.inside_count(local_ts(9, 1)) == 0


def test_one_count_after_confirmed_in_entry():
    tracker = make_tracker()
    results = see(tracker, random_encoding(), local_ts(9))
    assert events(results) == ["CANDIDATE", "CANDIDATE", "ENTRY"]
    assert results[-1].inside_count == 1 and results[-1].occupant_id is not None
    assert tracker.snapshot(local_ts(9, 1))["unknown_entries_today"] == 1


def test_repeated_sightings_do_not_inflate_the_count():
    tracker = make_tracker()
    person = random_encoding()
    results = see(tracker, person, local_ts(9), frames=200, jitter=0.15)
    assert events(results).count("ENTRY") == 1
    assert set(events(results)[3:]) == {"REFRESHED"}
    assert tracker.inside_count(local_ts(9, 1)) == 1


def test_a_gap_restarts_the_consecutive_count():
    tracker = make_tracker()
    person = random_encoding()
    see(tracker, person, local_ts(9), frames=2)
    # 5 s later (> max_frame_gap): starts over, so two more sightings are not enough
    assert events(see(tracker, person, local_ts(9, 0, 5), frames=2)) == ["CANDIDATE", "CANDIDATE"]
    assert tracker.inside_count(local_ts(9, 1)) == 0


def test_same_person_at_a_second_in_camera_is_not_double_counted():
    tracker = make_tracker()
    person = random_encoding()
    see(tracker, person, local_ts(9), camera_key="in-1")
    results = see(tracker, person, local_ts(9, 0, 10), camera_key="in-2", jitter=0.2)
    assert events(results)[-1] == "REFRESHED"
    assert tracker.inside_count(local_ts(9, 1)) == 1


def test_multiple_distinct_unknown_people_are_counted_separately():
    tracker = make_tracker()
    for i in range(4):
        see(tracker, random_encoding(), local_ts(9, 0, i))
    assert tracker.inside_count(local_ts(9, 1)) == 4


def test_interleaved_sightings_of_two_people_in_one_frame_stream():
    tracker = make_tracker()
    a, b = random_encoding(), random_encoding()
    t = local_ts(9)
    results = []
    for i in range(3):  # both faces in every frame
        results.append(tracker.observe(UNKNOWN, a, t + i * 0.1, "IN", "in-1"))
        results.append(tracker.observe(UNKNOWN, b, t + i * 0.1, "IN", "in-1"))
    assert events(results).count("ENTRY") == 2
    assert tracker.inside_count(t + 1) == 2


# --- exit -----------------------------------------------------------------------------

def test_qualifying_out_observation_removes_one_matching_occupant():
    tracker = make_tracker()
    a, b = random_encoding(), random_encoding()
    see(tracker, a, local_ts(9))
    see(tracker, b, local_ts(9, 0, 1))
    results = see(tracker, a, local_ts(9, 5), camera_type="OUT", camera_key="out-1", jitter=0.2)
    assert events(results) == ["CANDIDATE", "CANDIDATE", "EXIT"]
    assert tracker.inside_count(local_ts(9, 6)) == 1
    # The person still inside is b
    assert events(see(tracker, b, local_ts(9, 7)))[-1] == "REFRESHED"


def test_out_observation_before_minimum_presence_changes_nothing():
    tracker = make_tracker(min_presence_seconds=60.0)
    person = random_encoding()
    see(tracker, person, local_ts(9))
    # e.g. both doorway cameras see the person at the same moment
    results = see(tracker, person, local_ts(9, 0, 20), camera_type="OUT", camera_key="out-1")
    assert results[-1].event == "NO_CHANGE" and "min 60.0s" in results[-1].reasoning
    assert tracker.inside_count(local_ts(9, 1)) == 1


def test_out_observation_with_no_matching_occupant_changes_nothing():
    tracker = make_tracker()
    see(tracker, random_encoding(), local_ts(9))
    results = see(tracker, random_encoding(), local_ts(9, 5), camera_type="OUT", camera_key="out-1")
    assert results[-1].event == "NO_CHANGE"
    assert tracker.inside_count(local_ts(9, 6)) == 1


def test_count_never_goes_negative():
    tracker = make_tracker()
    for i in range(5):  # exits with nobody inside
        results = see(tracker, random_encoding(), local_ts(9, i), camera_type="OUT", camera_key="out-1")
        assert results[-1].event == "NO_CHANGE" and results[-1].inside_count == 0
    person = random_encoding()
    see(tracker, person, local_ts(10))
    see(tracker, person, local_ts(11), camera_type="OUT", camera_key="out-1")
    later = see(tracker, person, local_ts(11, 0, 30), camera_type="OUT", camera_key="out-1")
    assert later[-1].event == "NO_CHANGE"  # already exited: no second decrement
    assert tracker.inside_count(local_ts(12)) == 0


def test_out_sightings_also_need_consecutive_frames():
    tracker = make_tracker()
    person = random_encoding()
    see(tracker, person, local_ts(9))
    results = see(tracker, person, local_ts(9, 5), camera_type="OUT", camera_key="out-1", frames=2)
    assert events(results) == ["CANDIDATE", "CANDIDATE"]
    assert tracker.inside_count(local_ts(9, 6)) == 1


def test_reentry_respects_the_exit_cooldown():
    tracker = make_tracker(exit_cooldown=60.0)
    person = random_encoding()
    see(tracker, person, local_ts(9))
    see(tracker, person, local_ts(9, 5), camera_type="OUT", camera_key="out-1")
    blocked = see(tracker, person, local_ts(9, 5, 30), camera_key="in-1")
    assert blocked[-1].event == "NO_CHANGE" and "Exited less than" in blocked[-1].reasoning
    assert tracker.inside_count(local_ts(9, 6)) == 0
    allowed = see(tracker, person, local_ts(9, 7), camera_key="in-1")
    assert allowed[-1].event == "ENTRY"
    assert tracker.inside_count(local_ts(9, 8)) == 1
    assert tracker.snapshot(local_ts(9, 8))["unknown_entries_today"] == 2


# --- input contract -------------------------------------------------------------------

@pytest.mark.parametrize("kind", [AMBIGUOUS, KNOWN])
def test_ambiguous_and_known_faces_are_ignored(kind):
    tracker = make_tracker()
    results = see(tracker, random_encoding(), local_ts(9), frames=50, kind=kind)
    assert set(events(results)) == {"IGNORED"}
    assert tracker.inside_count(local_ts(9, 1)) == 0
    assert tracker.candidate_count("in-1") == 0


def test_known_resolution_removes_a_matching_unknown_occupant():
    tracker = make_tracker()
    person, other = random_encoding(), random_encoding()
    see(tracker, person, local_ts(9))
    see(tracker, other, local_ts(9, 0, 1))
    see(tracker, person, local_ts(9, 0, 2), camera_key="in-2", frames=2)  # pending candidate elsewhere
    # Later the same face is recognised as a registered student
    removed = tracker.resolve_known(at_distance(person, 0.2), local_ts(9, 1))
    assert removed == 1
    assert tracker.inside_count(local_ts(9, 2)) == 1
    assert tracker.candidate_count("in-2") == 0
    assert tracker.snapshot(local_ts(9, 2))["unknown_entries_today"] == 1
    assert tracker.resolve_known(random_encoding(), local_ts(9, 3)) == 0


# --- lifecycle --------------------------------------------------------------------------

def test_clearing_a_camera_drops_its_candidates_but_keeps_occupants():
    tracker = make_tracker()
    inside, pending = random_encoding(), random_encoding()
    see(tracker, inside, local_ts(9), camera_key="in-1")
    see(tracker, pending, local_ts(9, 0, 1), camera_key="in-1", frames=2)
    see(tracker, pending, local_ts(9, 0, 1), camera_key="in-2", frames=1)
    assert tracker.candidate_count("in-1") == 2

    tracker.clear_camera("in-1")
    assert tracker.candidate_count("in-1") == 0
    assert tracker.candidate_count("in-2") == 1  # other cameras untouched
    assert tracker.inside_count(local_ts(9, 0, 2)) == 1
    # The cleared candidate starts over
    assert events(see(tracker, pending, local_ts(9, 0, 3), camera_key="in-1", frames=1)) == ["CANDIDATE"]


def test_local_day_change_resets_occupancy():
    tracker = make_tracker(now=local_ts(8, days_ago=1))
    see(tracker, random_encoding(), local_ts(23, 59, days_ago=1))
    assert tracker.inside_count(local_ts(23, 59, 30, days_ago=1)) == 1
    snapshot = tracker.snapshot(local_ts(0, 5))
    assert snapshot["unknown_inside_count"] == 0 and snapshot["unknown_entries_today"] == 0
    # Yesterday's unknown leaving today is not subtracted from today's count
    person = random_encoding()
    see(tracker, person, local_ts(9))
    assert tracker.inside_count(local_ts(9, 1)) == 1


def test_process_restart_means_a_new_empty_tracker():
    first = get_shared_unknown_tracker(min_consecutive_frames=3)
    see(first, random_encoding(), local_ts(9))
    assert get_shared_unknown_tracker() is first and first.inside_count(local_ts(9, 1)) == 1
    reset_shared_unknown_tracker()  # what a restart amounts to: state is memory only
    second = get_shared_unknown_tracker(now=local_ts(10))
    assert second is not first
    assert second.snapshot(local_ts(10)) == {
        "unknown_inside_count": 0, "unknown_entries_today": 0, "unknown_tracking_since": local_ts(10)
    }


def test_snapshot_exposes_counts_only():
    tracker = make_tracker()
    see(tracker, random_encoding(), local_ts(9))
    snapshot = tracker.snapshot(local_ts(9, 1))
    assert set(snapshot) == {"unknown_inside_count", "unknown_entries_today", "unknown_tracking_since"}
    assert all(not isinstance(v, np.ndarray) for v in snapshot.values())


def test_thresholds_come_from_settings():
    settings = Settings(_env_file=None, ENVIRONMENT="test", SECRET_KEY="x" * 64,
                        UNKNOWN_SAME_PERSON_DISTANCE=0.5, UNKNOWN_RESOLVE_KNOWN_DISTANCE=0.3,
                        ATTENDANCE_MIN_CONSECUTIVE_FRAMES=7,
                        ATTENDANCE_MAX_FRAME_GAP_SECONDS=3.0, ATTENDANCE_EXIT_COOLDOWN_SECONDS=90.0,
                        ATTENDANCE_MIN_PRESENCE_SECONDS=45.0)
    tracker = UnknownOccupancyTracker.from_settings(settings, now=local_ts(8))
    assert (tracker.same_person_distance, tracker.resolve_known_distance, tracker.min_consecutive_frames,
            tracker.max_frame_gap, tracker.exit_cooldown, tracker.min_presence_seconds) == (0.5, 0.3, 7, 3.0, 90.0, 45.0)
    defaults = Settings(_env_file=None, ENVIRONMENT="test", SECRET_KEY="x" * 64)
    assert (defaults.UNKNOWN_SAME_PERSON_DISTANCE, defaults.UNKNOWN_RESOLVE_KNOWN_DISTANCE) == (0.6, 0.45)
    # The default settings build a valid tracker
    assert UnknownOccupancyTracker.from_settings(defaults).resolve_known_distance == 0.45


# --- exact boundaries -------------------------------------------------------------------
# Encodings built from zeros and exactly representable offsets (0.25, 0.5) and timestamps
# offset by powers of two, so "exactly at the threshold" really is exact in floating point.

def axis(scale, index=0):
    vector = np.zeros(128)
    vector[index] = scale
    return vector


def confirm_at(tracker, encoding, confirm_ts, camera_type="IN", camera_key="in-1"):
    """Three sightings 0.5 s apart, the confirming one at exactly confirm_ts"""
    return see(tracker, encoding, confirm_ts - 1.0, camera_type, camera_key, frames=3, step=0.5)


@pytest.mark.parametrize("offset, expected", [(0.5, "EXIT"), (0.5 + 1e-9, "NO_CHANGE")])
def test_same_person_distance_boundary_is_inclusive(offset, expected):
    tracker = make_tracker(same_person_distance=0.5, resolve_known_distance=0.25)
    t0 = local_ts(9)
    assert confirm_at(tracker, axis(0), t0)[-1].event == "ENTRY"
    out = confirm_at(tracker, axis(offset), t0 + 100, "OUT", "out-1")
    assert out[-1].event == expected


@pytest.mark.parametrize("offset, removed", [(0.25, 1), (0.25 + 1e-9, 0)])
def test_resolve_known_distance_boundary_is_inclusive(offset, removed):
    tracker = make_tracker(same_person_distance=0.5, resolve_known_distance=0.25)
    t0 = local_ts(9)
    confirm_at(tracker, axis(0), t0)
    assert tracker.resolve_known(axis(offset), t0 + 5) == removed
    assert tracker.inside_count(t0 + 6) == 1 - removed


@pytest.mark.parametrize("gap, expected", [(2.0, "ENTRY"), (2.0 + 2 ** -10, "CANDIDATE")])
def test_frame_gap_boundary(gap, expected):
    tracker = make_tracker()  # max_frame_gap=2.0, 3 frames
    t0 = local_ts(9)
    tracker.observe(UNKNOWN, axis(0), t0, "IN", "in-1")
    tracker.observe(UNKNOWN, axis(0), t0 + 0.5, "IN", "in-1")
    # A gap of exactly max_frame_gap keeps the run going; anything longer restarts it
    assert tracker.observe(UNKNOWN, axis(0), t0 + 0.5 + gap, "IN", "in-1").event == expected


@pytest.mark.parametrize("after_exit, expected", [(60.0, "ENTRY"), (60.0 - 2 ** -10, "NO_CHANGE")])
def test_exit_cooldown_boundary(after_exit, expected):
    tracker = make_tracker()  # exit_cooldown=60, min_presence=60
    t0 = local_ts(9)
    confirm_at(tracker, axis(0), t0)
    exit_ts = t0 + 100
    assert confirm_at(tracker, axis(0), exit_ts, "OUT", "out-1")[-1].event == "EXIT"
    # Re-entry is blocked for less than exit_cooldown seconds, allowed at exactly exit_cooldown
    assert confirm_at(tracker, axis(0), exit_ts + after_exit)[-1].event == expected


@pytest.mark.parametrize("inside_for, expected", [(60.0, "EXIT"), (60.0 - 2 ** -10, "NO_CHANGE")])
def test_min_presence_boundary(inside_for, expected):
    tracker = make_tracker()  # min_presence=60
    entered = local_ts(9)
    assert confirm_at(tracker, axis(0), entered)[-1].event == "ENTRY"
    assert confirm_at(tracker, axis(0), entered + inside_for, "OUT", "out-1")[-1].event == expected


# --- resolve_known: nearest only --------------------------------------------------------

def test_resolve_known_removes_only_the_nearest_of_several_nearby_occupants():
    tracker = make_tracker()  # same_person 0.6, resolve_known 0.45 (default)
    t0 = local_ts(9)
    known = axis(0)
    a = axis(0.25)    # 0.25 from the recognised face
    b = axis(-0.375)  # 0.375 from it, 0.625 from A: a separate occupant
    assert confirm_at(tracker, a, t0)[-1].event == "ENTRY"
    assert confirm_at(tracker, b, t0 + 5, camera_key="in-2")[-1].event == "ENTRY"

    assert tracker.resolve_known(known, t0 + 10) == 1
    snap = tracker.snapshot(t0 + 11)
    assert snap["unknown_inside_count"] == 1 and snap["unknown_entries_today"] == 1
    # The nearer one (A) was removed; B is still inside and can still exit
    assert confirm_at(tracker, a, t0 + 100, "OUT", "out-1")[-1].event == "NO_CHANGE"
    assert confirm_at(tracker, b, t0 + 110, "OUT", "out-2")[-1].event == "EXIT"


def test_resolve_known_removes_only_the_nearest_candidate_per_camera():
    tracker = make_tracker()
    t0 = local_ts(9)
    tracker.observe(UNKNOWN, axis(0.25), t0, "IN", "in-1")
    tracker.observe(UNKNOWN, axis(-0.375), t0 + 0.1, "IN", "in-1")
    tracker.observe(UNKNOWN, axis(0.25), t0 + 0.2, "IN", "in-2")
    assert tracker.candidate_count("in-1") == 2

    tracker.resolve_known(axis(0), t0 + 0.3)
    assert tracker.candidate_count("in-1") == 1
    assert tracker.candidate_count("in-2") == 0


def test_nearby_unrelated_occupant_stays_counted():
    tracker = make_tracker()
    t0 = local_ts(9)
    confirm_at(tracker, axis(0.55), t0)  # within same_person (0.6) but beyond resolve_known (0.45)
    assert tracker.resolve_known(axis(0), t0 + 5) == 0
    snap = tracker.snapshot(t0 + 6)
    assert snap["unknown_inside_count"] == 1 and snap["unknown_entries_today"] == 1


# --- known limitation and drift -------------------------------------------------------

def test_similar_looking_people_merge_into_one_occupant():
    # Documented limitation, not desired behaviour: two different unregistered people whose
    # encodings are within same_person_distance cannot be told apart and count as one.
    tracker = make_tracker()
    t0 = local_ts(9)
    assert confirm_at(tracker, axis(0), t0)[-1].event == "ENTRY"
    assert confirm_at(tracker, axis(0.5), t0 + 5, camera_key="in-2")[-1].event == "REFRESHED"
    assert tracker.inside_count(t0 + 6) == 1


def test_distant_raw_sightings_do_not_drift_the_occupant_encoding():
    tracker = make_tracker()
    t0 = local_ts(9)
    a = axis(0)
    assert confirm_at(tracker, a, t0)[-1].event == "ENTRY"

    # Each cycle: two frames 0.4 away, then a confirming frame 0.85 away. The candidate mean
    # (0.55) matches the occupant, but the raw 0.85 frame does not, so it must not be blended
    # in. Unguarded, 10 cycles would move the occupant's mean to ~0.65 along this axis.
    for cycle in range(10):
        start = t0 + 10 + cycle * 5  # > max_frame_gap between cycles: a fresh candidate each time
        results = [tracker.observe(UNKNOWN, axis(offset), start + i * 0.1, "IN", "in-2")
                   for i, offset in enumerate((0.4, 0.4, 0.85))]
        assert events(results) == ["CANDIDATE", "CANDIDATE", "REFRESHED"]

    # Someone 1.0 away (would be ~0.35 from a drifted mean) must not exit person A
    assert confirm_at(tracker, axis(1.0), t0 + 200, "OUT", "out-1")[-1].event == "NO_CHANGE"
    assert confirm_at(tracker, a, t0 + 210, "OUT", "out-2")[-1].event == "EXIT"


# --- midnight / out-of-order timestamps ----------------------------------------------------

def test_out_of_order_earlier_day_timestamps_never_reset_occupancy():
    tracker = make_tracker()
    person = axis(0)
    just_after_midnight = local_ts(0, 0, 5)
    yesterday_late = local_ts(23, 59, 59, days_ago=1)
    assert confirm_at(tracker, person, just_after_midnight)[-1].event == "ENTRY"

    # Late frames stamped before midnight (e.g. a slow worker) are ignored and reset nothing
    for kind in (KNOWN, AMBIGUOUS, UNKNOWN):
        result = tracker.observe(kind, random_encoding(), yesterday_late, "IN", "in-1")
        assert result.event == "IGNORED" and result.inside_count == 1
    assert tracker.inside_count(yesterday_late) == 1
    assert tracker.snapshot(yesterday_late)["unknown_entries_today"] == 1
    assert tracker.resolve_known(person, yesterday_late) == 0

    snap = tracker.snapshot(just_after_midnight + 10)
    assert snap["unknown_inside_count"] == 1 and snap["unknown_entries_today"] == 1
    # State survived: the same person is still recognised as inside today
    assert confirm_at(tracker, person, just_after_midnight + 20)[-1].event == "REFRESHED"

    # The day still rolls forward normally
    tomorrow = local_ts(0, 0, 1, days_ago=-1)
    assert tracker.snapshot(tomorrow) == {"unknown_inside_count": 0, "unknown_entries_today": 0,
                                          "unknown_tracking_since": tracker.tracking_since}


# --- constructor validation ---------------------------------------------------------------

@pytest.mark.parametrize("overrides", [
    dict(same_person_distance=0), dict(same_person_distance=-0.1),
    dict(same_person_distance=float("nan")), dict(same_person_distance=float("inf")),
    dict(same_person_distance=True), dict(same_person_distance="0.6"), dict(same_person_distance=None),
    dict(resolve_known_distance=0), dict(resolve_known_distance=-0.45),
    dict(resolve_known_distance=float("nan")), dict(resolve_known_distance=0.61),
    dict(same_person_distance=0.4),  # smaller than the default resolve_known_distance 0.45
    dict(min_consecutive_frames=0), dict(min_consecutive_frames=-1), dict(min_consecutive_frames=2.5),
    dict(min_consecutive_frames=3.0), dict(min_consecutive_frames=True), dict(min_consecutive_frames=None),
    dict(max_frame_gap=0), dict(max_frame_gap=-1), dict(max_frame_gap=float("nan")),
    dict(max_frame_gap=float("inf")),
    dict(exit_cooldown=-1), dict(exit_cooldown=float("nan")), dict(exit_cooldown=float("inf")),
    dict(min_presence_seconds=-0.5), dict(min_presence_seconds=float("nan")),
    dict(min_presence_seconds=float("inf")),
], ids=repr)
def test_invalid_settings_are_rejected(overrides):
    with pytest.raises(ValueError):
        make_tracker(**overrides)


def test_edge_valid_settings_are_accepted():
    tracker = make_tracker(min_consecutive_frames=1, exit_cooldown=0, min_presence_seconds=0,
                           same_person_distance=0.45, resolve_known_distance=0.45)
    assert tracker.resolve_known_distance == tracker.same_person_distance == 0.45


# --- concurrency ------------------------------------------------------------------------

def test_concurrent_updates_keep_counts_consistent():
    """IN, OUT, resolve_known and snapshot readers hammer one tracker with overlapping
    timestamps while the interpreter switches threads as often as possible. This checks the
    count invariants under heavy interleaving; it does not prove every race is impossible."""
    tracker = make_tracker(min_presence_seconds=0.5)
    people = [random_encoding() for _ in range(6)]
    t0 = local_ts(10)
    results, resolved, violations = [], [], []
    lock = threading.Lock()
    stop_reading = threading.Event()
    barrier = threading.Barrier(len(people) * 2 + 2)

    def camera_worker(person, camera_type, camera_key, start):
        barrier.wait(timeout=10)
        local = [tracker.observe(UNKNOWN, at_distance(person, 0.1), start + i * 0.1, camera_type, camera_key)
                 for i in range(40)]
        with lock:
            results.extend(local)

    def resolver():
        barrier.wait(timeout=10)
        local = [tracker.resolve_known(people[i % 2], t0 + 0.5 + i * 0.2) for i in range(20)]
        with lock:
            resolved.extend(local)

    def reader():
        barrier.wait(timeout=10)
        while not stop_reading.is_set():
            snap = tracker.snapshot(t0 + 2)
            if not 0 <= snap["unknown_inside_count"] <= snap["unknown_entries_today"]:
                violations.append(snap)

    threads = [threading.Thread(target=resolver), threading.Thread(target=reader)]
    for i, person in enumerate(people):
        # Same people seen by IN and OUT at overlapping times; cameras shared between people
        threads.append(threading.Thread(target=camera_worker, args=(person, "IN", f"in-{i % 2}", t0)))
        threads.append(threading.Thread(target=camera_worker, args=(person, "OUT", f"out-{i % 2}", t0 + 1)))

    original_interval = sys.getswitchinterval()
    try:
        sys.setswitchinterval(1e-6)
        for t in threads:
            t.start()
        for t in threads[:1] + threads[2:]:
            t.join(timeout=60)
        stop_reading.set()
        threads[1].join(timeout=60)
    finally:
        stop_reading.set()
        sys.setswitchinterval(original_interval)
    assert not any(t.is_alive() for t in threads)

    entries = [r.occupant_id for r in results if r.event == "ENTRY"]
    exits = [r.occupant_id for r in results if r.event == "EXIT"]
    snap = tracker.snapshot(t0 + 10)
    assert violations == []
    assert all(r.inside_count >= 0 for r in results)
    assert len(entries) == len(set(entries))  # every occupant id enters once
    assert len(exits) == len(set(exits)) and set(exits) <= set(entries)  # exits at most once
    assert snap["unknown_inside_count"] == len(entries) - len(exits) - sum(resolved) >= 0
    assert snap["unknown_entries_today"] == len(entries) - sum(resolved)
