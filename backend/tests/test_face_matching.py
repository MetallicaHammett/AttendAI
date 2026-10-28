"""
Face matching: distance metric, configurable tolerance, ambiguity, and
known-match vs unknown-person behaviour through to attendance decisions.

Encodings here are synthetic 128-d vectors placed at controlled Euclidean
distances; they verify the decision logic, not real-camera accuracy.
"""
import threading

import numpy as np
import pytest

from app.config import settings
from app.services import AttendanceEngine, DetectionStatus, FaceDetectionService

RNG = np.random.default_rng(1234)


def unit(v):
    return v / np.linalg.norm(v)


def random_encoding():
    # Real dlib encodings have values roughly in [-0.3, 0.3]; scale is irrelevant to the logic
    return RNG.normal(0, 0.09, 128)


def at_distance(base, distance):
    """An encoding exactly `distance` (L2) away from base"""
    return base + unit(RNG.normal(size=128)) * distance


def service(tolerance=0.6, margin=0.0, known=()):
    s = FaceDetectionService(match_tolerance=tolerance, ambiguity_margin=margin)
    s.load_known_faces(list(known))
    return s


def test_distance_metric_is_euclidean_and_matches_face_recognition():
    known = [random_encoding() for _ in range(5)]
    probe = random_encoding()
    ours = FaceDetectionService.face_distances(known, probe)
    np.testing.assert_allclose(ours, [np.linalg.norm(k - probe) for k in known])
    try:
        import face_recognition
    except BaseException as e:  # face_recognition calls quit() if its models package is broken
        pytest.skip(f"face_recognition not importable in this environment: {type(e).__name__}")
    np.testing.assert_allclose(ours, face_recognition.face_distance(known, probe))


def test_known_match_returns_student_and_confidence():
    alice = random_encoding()
    s = service(known=[(1, alice)])
    result = s.recognize_faces([at_distance(alice, 0.25)])
    assert result.status == DetectionStatus.SUCCESS
    assert result.student_ids == [1]
    assert result.confidences[0] == pytest.approx(75.0)  # (1 - 0.25) * 100


def test_unknown_person_beyond_tolerance():
    alice = random_encoding()
    s = service(known=[(1, alice)])
    result = s.recognize_faces([at_distance(alice, 0.75)])
    assert result.status == DetectionStatus.UNKNOWN_PERSON
    assert result.student_ids == [None]
    assert result.confidences == [0.0]


def test_tolerance_boundary_and_configurability():
    alice = random_encoding()
    probe = at_distance(alice, 0.5)
    assert service(tolerance=0.6, known=[(1, alice)]).recognize_faces([probe]).student_ids == [1]
    assert service(tolerance=0.5 + 1e-9, known=[(1, alice)]).recognize_faces([probe]).student_ids == [1]
    assert service(tolerance=0.45, known=[(1, alice)]).recognize_faces([probe]).student_ids == [None]


def test_ambiguity_margin_rejects_near_ties_between_students():
    alice, bob = random_encoding(), random_encoding()
    probe = random_encoding()
    # Place Alice's and Bob's enrollments 0.40 and 0.42 from the probe
    known = [(1, at_distance(probe, 0.40)), (2, at_distance(probe, 0.42))]
    assert service(margin=0.0, known=known).recognize_faces([probe]).student_ids == [1]
    assert service(margin=0.05, known=known).recognize_faces([probe]).student_ids == [None]


def test_exact_tie_between_students_is_ambiguous_with_default_margin():
    probe = random_encoding()
    d = unit(RNG.normal(size=128)) * 0.3
    known = [(1, probe + d), (2, probe - d)]  # both exactly 0.3 away
    result = service(known=known).recognize_faces([probe])
    assert result.student_ids == [None] and result.confidences == [0.0]


def test_multiple_enrollments_of_same_student_are_not_ambiguous():
    probe = random_encoding()
    known = [(1, at_distance(probe, 0.30)), (1, at_distance(probe, 0.31)), (2, at_distance(probe, 0.55))]
    result = service(margin=0.05, known=known).recognize_faces([probe])
    assert result.student_ids == [1]
    assert result.confidences[0] == pytest.approx(70.0)


def test_runner_up_outside_tolerance_does_not_make_match_ambiguous():
    probe = random_encoding()
    known = [(1, at_distance(probe, 0.58)), (2, at_distance(probe, 0.61))]
    assert service(margin=0.05, known=known).recognize_faces([probe]).student_ids == [1]


def test_no_enrolled_faces_means_everyone_unknown():
    result = service().recognize_faces([random_encoding(), random_encoding()])
    assert result.student_ids == [None, None]
    assert result.status == DetectionStatus.UNKNOWN_PERSON


def run_decisions(match_service, probe, frames=5, threshold=None):
    engine = AttendanceEngine(
        confidence_threshold=settings.ATTENDANCE_MIN_CONFIDENCE if threshold is None else threshold,
        min_consecutive_frames=3)
    out = []
    for i in range(frames):
        r = match_service.recognize_faces([probe])
        out.append(engine.process_recognition(r.student_ids[0], r.confidences[0], 1_000_000 + i * 0.1, "IN", camera_id=1))
    return out


def test_known_match_produces_entry_and_unknown_does_not():
    alice = random_encoding()
    s = service(tolerance=settings.FACE_MATCH_TOLERANCE, known=[(1, alice)])
    known = run_decisions(s, at_distance(alice, 0.2))
    unknown = run_decisions(s, at_distance(alice, 0.9))
    assert [d.event_type for d in known if d.should_record] == ["ENTRY"]
    assert not any(d.should_record for d in unknown)
    assert all(d.event_type == "UNKNOWN" for d in unknown)


def test_default_settings_require_distance_at_most_0_30_to_record():
    """Documents current defaults: a candidate at distance 0.35 matches (<= 0.6)
    but is LOW_CONFIDENCE (65% < 70%), so it is not recorded. Tune with real footage."""
    assert settings.FACE_MATCH_TOLERANCE == 0.6
    assert settings.ATTENDANCE_MIN_CONFIDENCE == 70.0
    alice = random_encoding()
    s = service(known=[(1, alice)])
    decisions = run_decisions(s, at_distance(alice, 0.35))
    assert all(d.student_id == 1 and d.event_type == "LOW_CONFIDENCE" for d in decisions)
    decisions = run_decisions(s, at_distance(alice, 0.29))
    assert [d.event_type for d in decisions if d.should_record] == ["ENTRY"]


def test_reloading_known_faces_while_matching_is_safe():
    alice, bob = random_encoding(), random_encoding()
    s = service(known=[(1, alice)])
    errors, stop = [], threading.Event()

    def matcher():
        while not stop.is_set():
            r = s.recognize_faces([alice])
            if r.status == DetectionStatus.PROCESSING_ERROR or r.student_ids[0] not in (1, None):
                errors.append(r)

    threads = [threading.Thread(target=matcher) for _ in range(3)]
    for t in threads:
        t.start()
    for i in range(300):
        s.load_known_faces([(1, alice), (2, bob)] if i % 2 else [(1, alice)])
    stop.set()
    for t in threads:
        t.join()
    assert errors == []
