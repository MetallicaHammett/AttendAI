"""
Phase I scenarios that need whole camera pipelines rather than single components:
- IN and OUT cameras processing at the same time on real threads, sharing one attendance
  engine and one unknown-occupancy tracker (a student enters, another leaves, a visitor
  enters - concurrently)
- the recognition model failing mid-stream and recovering

Synthetic frames and fixed 128-d encodings stand in for camera images and dlib; person/face
detection are stubbed per camera. This proves the pipeline wiring and concurrency, NOT
recognition accuracy on real footage.
"""
import threading
import time

import numpy as np
import pytest

from app import services
from app.config import settings
from app.services import DetectionResult, DetectionStatus, FaceResult, TrackingResult

rng = np.random.default_rng(7)
ENC = {name: rng.normal(0, 0.08, 128) for name in ("asha", "bala", "visitor")}
IDS = {"asha": 1, "bala": 2}
FACE_BOX = [(100, 220, 220, 100)]
PERSON_BOX = [(80, 60, 300, 460)]


class EndlessFrames:
    """Fake recorded source: an endless supply of blank frames"""
    def __init__(self, *args):
        self.released = False

    def isOpened(self):
        return True

    def set(self, *args):
        return True

    def read(self):
        time.sleep(0.002)
        return True, np.zeros((480, 640, 3), np.uint8)

    def release(self):
        self.released = True


@pytest.fixture
def no_devices(monkeypatch):
    monkeypatch.setattr(services.cv2, "VideoCapture", lambda *a: EndlessFrames(*a))


def camera(camera_type, camera_id, faces, engine, tracker):
    """One camera pipeline that 'sees' the given faces (names) in every frame"""
    svc = services.VideoProcessingService(video_source=f"data/videos/{camera_type}.mp4", source_type="video",
                                          camera_type=camera_type, camera_id=camera_id, attendance_engine=engine,
                                          unknown_tracker=tracker)
    svc.person_detector.detect_persons = lambda frame: DetectionResult(
        PERSON_BOX * len(faces), [0.9] * len(faces), DetectionStatus.SUCCESS, frame.shape)
    svc.face_detector.detect_faces = lambda frame, boxes: FaceResult(
        FACE_BOX * len(faces), [ENC[f].copy() for f in faces], DetectionStatus.SUCCESS, boxes)
    svc.tracker.update_tracks = lambda frame, dets: TrackingResult(tracks=[], status=DetectionStatus.NO_PERSON)
    svc.face_detector.load_known_faces([(IDS["asha"], ENC["asha"]), (IDS["bala"], ENC["bala"])])
    return svc


def test_in_and_out_cameras_process_concurrently_without_duplicates(no_devices):
    engine = services.AttendanceEngine(min_consecutive_frames=3, min_presence_seconds=0, max_frame_gap=5)
    engine.seed_from_events([(IDS["bala"], "ENTRY", time.time() - 600)])  # Bala came in earlier today
    tracker = services.UnknownOccupancyTracker.from_settings(settings)
    door_in = camera("IN", 1, ["asha", "visitor"], engine, tracker)   # Asha and a visitor walk in together
    door_out = camera("OUT", 2, ["bala"], engine, tracker)             # Bala leaves at the same time
    recorded, errors = [], []
    lock = threading.Lock()

    def record(decision):
        with lock:
            recorded.append((decision.student_id, decision.event_type, decision.camera_id))

    def run(svc):
        try:
            svc.start()
            for _ in range(25):
                svc.process_frame(None, record_event=record)
        except Exception as e:  # surfaced below
            errors.append(e)
        finally:
            svc.stop()

    threads = [threading.Thread(target=run, args=(svc,)) for svc in (door_in, door_out)]
    started = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors and not any(t.is_alive() for t in threads)
    # Exactly one ENTRY for Asha at the IN camera, one EXIT for Bala at the OUT camera - no
    # duplicates despite 25 sightings each, and the visitor never becomes a student event
    assert sorted(recorded) == [(IDS["asha"], "ENTRY", 1), (IDS["bala"], "EXIT", 2)]
    assert engine.inside_count() == 1                      # Asha inside, Bala gone
    assert tracker.inside_count() == 1                     # one unregistered person inside
    assert door_in.frame_count == 25 and door_out.frame_count == 25
    assert time.perf_counter() - started < 20


def test_model_failure_mid_stream_is_reported_and_recovers(no_devices):
    engine = services.AttendanceEngine(min_consecutive_frames=3, max_frame_gap=5)
    svc = camera("IN", 1, ["asha"], engine, None)
    working = svc.person_detector.detect_persons
    calls = {"n": 0}

    def flaky(frame):
        calls["n"] += 1
        if 3 <= calls["n"] <= 6:
            raise RuntimeError("model crashed")
        return working(frame)

    svc.person_detector.detect_persons = flaky
    recorded = []
    svc.start()
    statuses = []
    for _ in range(12):
        frame, _ = svc.process_frame(None, record_event=recorded.append)
        assert frame is not None  # the live view keeps showing the camera
        stats = svc.get_stats()
        statuses.append((stats["pipeline_error"], stats["state"]))
    svc.stop()
    assert any(err and "RuntimeError" in err for err, _ in statuses[2:6])  # reported, not hidden
    assert statuses[-1] == (None, "stopped") or statuses[-1][0] is None   # cleared after recovery
    assert all(state in ("running", "stopped") for _, state in statuses)  # the camera itself stays up
    # Failed frames never count towards confirmation; one ENTRY once recognition works again
    assert [d.event_type for d in recorded] == ["ENTRY"]
