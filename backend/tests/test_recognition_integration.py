"""
KNOWN / UNKNOWN / AMBIGUOUS classification and its integration into the camera pipeline:
unknown faces drive the shared UnknownOccupancyTracker, ambiguous faces are never counted,
a confidently recognised student removes a matching unknown occupant, and unknown
visitors never create student attendance records.

Person/face detection are stubs that return chosen encodings (synthetic 128-d vectors).
These tests prove the plumbing and decision rules - NOT recognition accuracy on real
faces, which needs labelled real footage (see docs/EVALUATION.md).
"""
import numpy as np
import pytest

from app import services
from app.services import (DetectionResult, DetectionStatus, FaceDetectionService, FaceObservationKind, FaceResult,
                          UnknownOccupancyTracker, VideoProcessingService)

RNG = np.random.default_rng(11)


def enc():
    return RNG.normal(0, 0.09, 128)


def near(base, distance):
    d = RNG.normal(size=128)
    return base + d / np.linalg.norm(d) * distance


# --- classification -------------------------------------------------------------------

def test_classification_kinds():
    detector = FaceDetectionService(match_tolerance=0.6, ambiguity_margin=0.05)
    alice, bob = enc(), enc()
    known = [alice, bob]
    match = detector.classify_encoding(near(alice, 0.2), known, [1, 2])
    assert match.kind == FaceObservationKind.KNOWN and match.student_id == 1
    assert match.confidence == pytest.approx((1 - match.distance) * 100)

    stranger = detector.classify_encoding(enc(), known, [1, 2])
    assert stranger.kind == FaceObservationKind.UNKNOWN and stranger.student_id is None

    nobody = detector.classify_encoding(enc(), [], [])
    assert nobody.kind == FaceObservationKind.UNKNOWN and nobody.distance is None

    # Equidistant from two different students -> ambiguous, never "unknown"
    midpoint = (alice + bob) / 2
    twins = [midpoint + 0.001 * np.eye(128)[0], midpoint - 0.001 * np.eye(128)[0]]
    ambiguous = detector.classify_encoding(midpoint, twins, [1, 2])
    assert ambiguous.kind == FaceObservationKind.AMBIGUOUS and ambiguous.student_id is None
    # Two encodings of the SAME student are never ambiguous
    same = detector.classify_encoding(midpoint, twins, [1, 1])
    assert same.kind == FaceObservationKind.KNOWN


def test_recognize_faces_reports_kinds_and_keeps_old_fields():
    detector = FaceDetectionService()
    alice = enc()
    detector.load_known_faces([(7, alice)])
    result = detector.recognize_faces([near(alice, 0.1), enc()])
    assert result.student_ids == [7, None]
    assert result.kinds == [FaceObservationKind.KNOWN, FaceObservationKind.UNKNOWN]
    empty = FaceDetectionService().recognize_faces([enc()])
    assert empty.kinds == [FaceObservationKind.UNKNOWN]


# --- pipeline -------------------------------------------------------------------------------

@pytest.fixture
def faces(monkeypatch):
    """The encodings the stubbed face detector "sees" in each frame (one per person)"""
    current = []
    monkeypatch.setattr(services.PersonDetectionService, "detect_persons", lambda self, frame: DetectionResult(
        boxes=[(10 + 60 * i, 10, 60 + 60 * i, 120) for i in range(len(current))],
        confidences=[0.9] * len(current),
        status=DetectionStatus.SUCCESS if current else DetectionStatus.NO_PERSON, frame_shape=frame.shape))
    monkeypatch.setattr(services.FaceDetectionService, "detect_faces", lambda self, frame, boxes: FaceResult(
        face_locations=[(20, 50 + 60 * i, 50, 20 + 60 * i) for i in range(len(current))],
        face_encodings=[e.copy() for e in current],
        status=DetectionStatus.SUCCESS if current else DetectionStatus.NO_FACE, person_boxes=boxes))
    monkeypatch.setattr(services.TrackingService, "update_tracks",
                        lambda self, frame, dets: services.TrackingResult(tracks=[], status=DetectionStatus.NO_PERSON))
    return current


def pipeline(camera_type, camera_id, tracker, engine, known=()):
    detector = FaceDetectionService(match_tolerance=0.6, ambiguity_margin=0.05)
    detector.load_known_faces(list(known))
    svc = VideoProcessingService(video_source="Z:/none.mp4", allow_synthetic_source=True, camera_type=camera_type,
                                 camera_id=camera_id, attendance_engine=engine, face_detector=detector,
                                 unknown_tracker=tracker)
    svc.start()
    return svc


def run(svc, frames, recorded):
    for _ in range(frames):
        svc.process_frame(None, record_event=recorded.append)


@pytest.fixture
def shared():
    tracker = UnknownOccupancyTracker(min_consecutive_frames=3, min_presence_seconds=0.0, exit_cooldown=60.0)
    engine = services.AttendanceEngine(min_consecutive_frames=3, min_presence_seconds=0.0)
    return tracker, engine


def test_unknown_visitor_counted_once_on_entry_and_removed_on_exit(faces, shared):
    tracker, engine = shared
    recorded = []
    visitor = enc()
    door_in, door_out = pipeline("IN", 1, tracker, engine), pipeline("OUT", 2, tracker, engine)

    faces[:] = [visitor]
    run(door_in, 20, recorded)  # many frames of the same person: still one entry
    assert tracker.inside_count() == 1 and tracker.snapshot()["unknown_entries_today"] == 1
    assert recorded == []  # unknown visitors never create student attendance

    faces[:] = [near(visitor, 0.15)]  # same person, slightly different at the exit camera
    run(door_out, 5, recorded)
    assert tracker.inside_count() == 0 and recorded == []

    events = [o for o in door_in.get_recent_observations() + door_out.get_recent_observations() if o["recorded"]]
    assert sorted(o["decision"] for o in events) == ["ENTRY", "EXIT"]
    assert all(o["kind"] == "unknown" and o["student_id"] is None for o in events)
    assert not any("encoding" in key for o in events for key in o)  # no biometric data in the log


def test_unrelated_face_at_exit_does_not_remove_a_visitor(faces, shared):
    tracker, engine = shared
    door_in, door_out = pipeline("IN", 1, tracker, engine), pipeline("OUT", 2, tracker, engine)
    faces[:] = [enc()]
    run(door_in, 5, [])
    faces[:] = [enc()]  # somebody else leaves
    run(door_out, 5, [])
    assert tracker.inside_count() == 1


def test_group_of_visitors_and_students_enter_together(faces, shared):
    tracker, engine = shared
    students = [(101, enc()), (102, enc())]
    visitors = [enc(), enc(), enc()]
    door_in = pipeline("IN", 1, tracker, engine, known=students)
    recorded = []
    faces[:] = [students[0][1], visitors[0], students[1][1], visitors[1], visitors[2]]
    run(door_in, 6, recorded)
    assert sorted(d.student_id for d in recorded) == [101, 102]
    assert all(d.event_type == "ENTRY" for d in recorded)
    assert tracker.inside_count() == 3


def test_ambiguous_faces_are_never_counted_as_unregistered(faces, shared):
    tracker, engine = shared
    a, b = enc(), enc()
    midpoint = (a + b) / 2
    twins = [(1, midpoint + 0.001 * np.eye(128)[0]), (2, midpoint - 0.001 * np.eye(128)[0])]
    door_in = pipeline("IN", 1, tracker, engine, known=twins)
    recorded = []
    faces[:] = [midpoint]
    run(door_in, 10, recorded)
    assert tracker.inside_count() == 0 and recorded == []
    assert door_in.get_stats()["total_ambiguous_seen"] == 10
    assert door_in.get_recent_observations()[0]["kind"] == "ambiguous"


def test_recognised_student_removes_their_earlier_unknown_count(faces, shared):
    """A student first counted as unregistered (e.g. before enrollment) is removed from
    the unknown count once confidently recognised - and only that one occupant."""
    tracker, engine = shared
    student_face, visitor = enc(), enc()
    door_in = pipeline("IN", 1, tracker, engine)
    faces[:] = [student_face, visitor]
    run(door_in, 5, [])
    assert tracker.inside_count() == 2

    door_in.face_detector.load_known_faces([(55, student_face)])  # enrolled meanwhile
    faces[:] = [near(student_face, 0.05)]
    recorded = []
    run(door_in, 5, recorded)
    assert [d.student_id for d in recorded] == [55]
    assert tracker.inside_count() == 1  # the visitor is still counted


def test_low_confidence_known_face_does_not_resolve_unknowns(faces):
    tracker = UnknownOccupancyTracker(min_consecutive_frames=3, min_presence_seconds=0.0)
    engine = services.AttendanceEngine(confidence_threshold=95.0, min_consecutive_frames=3)
    face = enc()
    door_in = pipeline("IN", 1, tracker, engine)
    faces[:] = [face]
    run(door_in, 5, [])
    door_in.face_detector.load_known_faces([(9, near(face, 0.3))])  # matches, but score ~70 < 95
    faces[:] = [face]
    run(door_in, 5, [])
    assert tracker.inside_count() == 1


def test_stopping_a_camera_keeps_confirmed_occupants(faces, shared):
    tracker, engine = shared
    door_in = pipeline("IN", 1, tracker, engine)
    faces[:] = [enc()]
    run(door_in, 5, [])
    faces[:] = [enc()]
    run(door_in, 2, [])  # an unconfirmed candidate
    assert tracker.candidate_count(1) >= 1
    door_in.stop()
    assert tracker.candidate_count(1) == 0 and tracker.inside_count() == 1
    assert door_in.get_stats()["state"] == "stopped"


def test_tracking_runs_even_without_faces(monkeypatch):
    calls = []
    monkeypatch.setattr(services.PersonDetectionService, "detect_persons", lambda self, frame: DetectionResult(
        [(1, 1, 30, 30)], [0.9], DetectionStatus.SUCCESS, frame.shape))
    monkeypatch.setattr(services.FaceDetectionService, "detect_faces", lambda self, frame, boxes: FaceResult(
        [], [], DetectionStatus.NO_FACE, boxes))
    monkeypatch.setattr(services.TrackingService, "update_tracks", lambda self, frame, dets: calls.append(len(dets))
                        or services.TrackingResult(tracks=[], status=DetectionStatus.NO_PERSON))
    svc = VideoProcessingService(video_source="Z:/none.mp4", allow_synthetic_source=True)
    svc.start()
    svc.process_frame(None)
    assert calls == [1]  # the person was tracked although no face was visible


def test_live_endpoint_and_dashboard_report_the_shared_unknown_count(api, monkeypatch):
    client, _ = api
    tracker = services.get_shared_unknown_tracker(min_consecutive_frames=1, min_presence_seconds=0.0)
    tracker.observe(FaceObservationKind.UNKNOWN, enc(), __import__("time").time(), "IN", "in-1")
    live = client.get("/video/live").json()
    assert live["occupancy"]["unknown_inside_count"] == 1 and live["occupancy"]["unknown_tracking_active"]
    assert live["sessions"] == [] and live["observations"] == []
    stats = client.get("/dashboard/stats").json()
    assert stats["unknown_inside_count"] == 1 and stats["unknown_tracking_since"] == tracker.tracking_since
    alerts = client.get("/dashboard/alerts").json()["alerts"]
    assert any(a["type"] == "UNREGISTERED_INSIDE" and "estimate" in a["basis"] for a in alerts)


def test_slow_frames_do_not_prevent_confirmation(faces, shared, monkeypatch):
    """With several faces a CPU frame can take longer than the 2 s frame gap. The camera
    widens the gap to 2.5x the measured frame interval, so a slow but continuous sequence
    of sightings still confirms the entry (known student and unregistered visitor)."""
    tracker, engine = shared
    student, visitor = enc(), enc()
    door_in = pipeline("IN", 1, tracker, engine, known=[(77, student)])
    clock = [1_000_000.0]
    monkeypatch.setattr(services.time, "time", lambda: clock[0])
    recorded = []
    faces[:] = [student, visitor]
    for _ in range(4):
        clock[0] += 3.0  # 3 s per frame - longer than the 2 s configured gap
        door_in.process_frame(None, record_event=recorded.append)
    assert door_in.adaptive_frame_gap() == pytest.approx(7.5)
    assert [d.student_id for d in recorded] == [77]
    assert tracker.inside_count(clock[0]) == 1


def test_adaptive_gap_is_capped_and_a_real_pause_still_breaks_the_run(faces, shared, monkeypatch):
    tracker, engine = shared
    student = enc()
    door_in = pipeline("IN", 1, tracker, engine, known=[(78, student)])
    clock = [2_000_000.0]
    monkeypatch.setattr(services.time, "time", lambda: clock[0])
    recorded = []
    faces[:] = [student]
    for step in (0.1, 0.1, 30.0, 0.1):  # a 30 s pause between sightings
        clock[0] += step
        door_in.process_frame(None, record_event=recorded.append)
    assert door_in.adaptive_frame_gap() <= services.VideoProcessingService.MAX_ADAPTIVE_GAP_SECONDS
    assert recorded == []  # the run restarted after the pause: 2 sightings, 3 needed
