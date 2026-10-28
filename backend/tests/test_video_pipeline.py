"""
VideoProcessingService and /video router: camera-open errors, synthetic frames
as an explicit dev option, frame publishing, per-worker DB sessions,
persistence through the shared engine, and reloading newly enrolled faces.

Detection/recognition models are replaced with stubs; these tests exercise the
pipeline plumbing, not detection accuracy on real footage.
"""
import asyncio
import time

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models, services
from app.config import settings
from app.database import Base, enable_sqlite_foreign_keys, get_db
from app.routers import video
from app.routers.auth import get_current_user
from app.services import (CameraOpenError, DetectionResult, DetectionStatus, FaceResult,
                          VideoProcessingService)

MISSING_SOURCE = "Z:/definitely/not/here.mp4"
ENCODING = np.random.default_rng(7).normal(0, 0.09, 128)


# --- stubs -------------------------------------------------------------------

def one_person(self, frame):
    return DetectionResult(boxes=[(100, 100, 300, 400)], confidences=[0.9],
                           status=DetectionStatus.SUCCESS, frame_shape=frame.shape)


def no_person(self, frame):
    return DetectionResult(boxes=[], confidences=[], status=DetectionStatus.NO_PERSON, frame_shape=frame.shape)


def one_face(self, frame, person_boxes):
    return FaceResult(face_locations=[(120, 220, 220, 120)], face_encodings=[ENCODING.copy()],
                      status=DetectionStatus.SUCCESS, person_boxes=person_boxes)


class FailingCapture:
    def read(self):
        return False, None

    def release(self):
        pass


@pytest.fixture
def stub_models(monkeypatch):
    """Person + face detection stubs; recognition (matching) stays real"""
    monkeypatch.setattr(services.PersonDetectionService, "detect_persons", one_person)
    monkeypatch.setattr(services.FaceDetectionService, "detect_faces", one_face)
    monkeypatch.setattr(services.TrackingService, "update_tracks",
                        lambda self, frame, dets: services.TrackingResult(tracks=[], status=DetectionStatus.NO_PERSON))


@pytest.fixture
def db_factory(tmp_path):
    engine = enable_sqlite_foreign_keys(
        create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}))
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


def add_student(db, sid="S1", active=True, encoding=None):
    student = models.Student(student_id=sid, name=f"Student {sid}", is_active=active)
    db.add(student)
    db.commit()
    if encoding is not None:
        db.add(models.FaceEmbedding(student_id=student.id, embedding_data=np.asarray(encoding, np.float64).tobytes()))
        db.commit()
    return student


def add_camera(db, source, camera_type="IN"):
    camera = models.Camera(name=f"{camera_type} cam", source=source, source_type="video", camera_type=camera_type)
    db.add(camera)
    db.commit()
    return camera


# --- VideoProcessingService --------------------------------------------------

def test_unopenable_camera_raises_clear_error_by_default():
    svc = VideoProcessingService(video_source=MISSING_SOURCE)
    with pytest.raises(CameraOpenError) as exc:
        svc.start()
    assert MISSING_SOURCE in str(exc.value)
    assert not svc.is_running and not svc.use_test_source
    assert svc.get_stats()["last_error"] and svc.get_stats()["camera_healthy"] is False


def test_synthetic_frames_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(services.PersonDetectionService, "detect_persons", no_person)
    svc = VideoProcessingService(video_source=MISSING_SOURCE, allow_synthetic_source=True)
    svc.start()
    assert svc.use_test_source and svc.get_stats()["synthetic_source"]
    frame, decisions = svc.process_frame(None)
    assert frame is not None and decisions == []


def test_frame_published_even_when_no_person(monkeypatch):
    """Regression: the live frame used to update only when a face was recognised"""
    monkeypatch.setattr(services.PersonDetectionService, "detect_persons", no_person)
    svc = VideoProcessingService(video_source=MISSING_SOURCE, allow_synthetic_source=True)
    svc.start()
    assert svc.get_latest_frame_jpeg() is None
    svc.process_frame(None)
    assert svc.get_latest_frame_jpeg()[:2] == b"\xff\xd8"  # JPEG magic


def test_read_failures_are_reported_not_replaced_with_black_frames(monkeypatch):
    svc = VideoProcessingService(video_source=0)
    svc.cap, svc.is_running = FailingCapture(), True
    called = []
    monkeypatch.setattr(services.PersonDetectionService, "detect_persons", lambda *a: called.append(1))
    for _ in range(svc.max_consecutive_failures):
        assert svc.process_frame(None) == (None, [])
    stats = svc.get_stats()
    assert called == []  # no detection on fabricated frames
    assert stats["camera_healthy"] is False and "stopped delivering frames" in stats["last_error"]


def test_process_frame_persists_entry_through_shared_engine(stub_models):
    engine = services.AttendanceEngine(min_consecutive_frames=3)
    face_detector = services.FaceDetectionService()
    face_detector.load_known_faces([(42, ENCODING)])
    svc = VideoProcessingService(video_source=MISSING_SOURCE, allow_synthetic_source=True, camera_id=5,
                                 attendance_engine=engine, face_detector=face_detector)
    svc.start()
    saved = []
    all_recorded = []
    for _ in range(6):
        _, decisions = svc.process_frame(None, record_event=saved.append)
        all_recorded.extend(decisions)
    assert [(d.student_id, d.event_type, d.camera_id) for d in saved] == [(42, "ENTRY", 5)]
    assert [d.event_type for d in all_recorded] == ["ENTRY"]
    assert abs(saved[0].timestamp - time.time()) < 60  # wall-clock timestamps
    assert svc.get_stats()["current_inside_count"] == 1


def test_load_known_faces_skips_inactive_students_and_bad_embeddings(db_factory):
    db = db_factory()
    active = add_student(db, "A", encoding=ENCODING)
    add_student(db, "I", active=False, encoding=ENCODING + 1)
    db.add(models.FaceEmbedding(student_id=active.id, embedding_data=b"\x00" * 16))  # corrupt
    db.commit()
    svc = VideoProcessingService(video_source=0)
    assert svc.load_known_faces_from_db(db) is True
    assert svc.face_detector.known_face_student_ids == [active.id]
    db.close()


# --- /video router -----------------------------------------------------------

@pytest.fixture
def client(db_factory, monkeypatch):
    monkeypatch.setattr(video, "SessionLocal", db_factory)
    monkeypatch.setattr(settings, "ATTENDANCE_MIN_CONSECUTIVE_FRAMES", 3)
    monkeypatch.setattr(settings, "FACE_RELOAD_INTERVAL_SECONDS", 3600.0)

    app = FastAPI()
    app.include_router(video.router, prefix="/video")

    def override_db():
        db = db_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: {"id": None, "username": "tester", "role": "operator"}
    c = ASGIClient(app)
    yield c
    for camera_id in list(video.active_sessions):
        c.post(f"/video/{camera_id}/stop")
    video.active_sessions.clear()


class ASGIClient:
    """Minimal sync client over httpx.ASGITransport. Used instead of
    fastapi.testclient.TestClient, which breaks with the installed
    starlette 0.27 + httpx >= 0.28 combination in some local environments."""

    def __init__(self, app):
        self.app = app

    def _request(self, method, url):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                return await http.request(method, url)
        return asyncio.run(send())

    def get(self, url):
        return self._request("GET", url)

    def post(self, url):
        return self._request("POST", url)


def wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_start_returns_503_with_reason_when_camera_cannot_open(client, db_factory, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_CAMERA", False)
    db = db_factory()
    camera = add_camera(db, MISSING_SOURCE)
    response = client.post(f"/video/{camera.id}/start")
    assert response.status_code == 503
    assert "could not be opened" in response.json()["detail"]
    assert MISSING_SOURCE in response.json()["detail"]
    assert camera.id not in video.active_sessions
    db.close()


def test_worker_records_entry_with_own_session_and_reloads_new_enrollment(client, db_factory, monkeypatch, stub_models):
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_CAMERA", True)
    db = db_factory()
    student = add_student(db, "S1")  # not enrolled yet -> recognised as unknown
    camera = add_camera(db, MISSING_SOURCE)
    camera_id, student_id = camera.id, student.id

    assert client.post(f"/video/{camera_id}/start").status_code == 200
    time.sleep(1.0)
    db.expire_all()
    assert db.query(models.AttendanceEvent).count() == 0

    # Enroll the face while the worker runs (as the face-enrollment router does)
    db.add(models.FaceEmbedding(student_id=student_id, embedding_data=ENCODING.tobytes()))
    db.commit()
    services.notify_known_faces_changed()

    def events():
        db.expire_all()
        return db.query(models.AttendanceEvent).all()

    assert wait_for(lambda: len(events()) >= 1), "worker never recorded the ENTRY"
    time.sleep(1.0)  # keep seeing the student: no duplicate ENTRY
    rows = events()
    assert [(e.student_id, e.camera_id, e.event_type) for e in rows] == [(student_id, camera_id, "ENTRY")]

    status = client.get(f"/video/{camera_id}/status").json()
    assert status["is_processing"] and status["error"] is None
    assert status["service_stats"]["current_inside_count"] == 1
    assert client.get(f"/video/{camera_id}/frame").headers["content-type"] == "image/jpeg"

    assert client.post(f"/video/{camera_id}/stop").status_code == 200
    db.close()


def test_out_camera_exits_only_after_entry_from_in_camera(client, db_factory, monkeypatch, stub_models):
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_CAMERA", True)
    monkeypatch.setattr(settings, "ATTENDANCE_MIN_PRESENCE_SECONDS", 0.5)
    db = db_factory()
    student = add_student(db, "S1", encoding=ENCODING)
    in_cam, out_cam = add_camera(db, MISSING_SOURCE, "IN"), add_camera(db, MISSING_SOURCE, "OUT")
    in_id, out_id, student_id = in_cam.id, out_cam.id, student.id

    def events():
        db.expire_all()
        return [(e.camera_id, e.event_type) for e in db.query(models.AttendanceEvent).order_by(models.AttendanceEvent.id)]

    # OUT camera alone: student has no active entry -> no EXIT
    assert client.post(f"/video/{out_id}/start").status_code == 200
    time.sleep(1.0)
    assert events() == []
    assert client.post(f"/video/{out_id}/stop").status_code == 200

    # IN camera records ENTRY
    assert client.post(f"/video/{in_id}/start").status_code == 200
    assert wait_for(lambda: events() == [(in_id, "ENTRY")])
    assert client.post(f"/video/{in_id}/stop").status_code == 200

    # OUT camera now records exactly one EXIT (shared engine state)
    assert client.post(f"/video/{out_id}/start").status_code == 200
    assert wait_for(lambda: len(events()) == 2)
    time.sleep(1.0)
    assert events() == [(in_id, "ENTRY"), (out_id, "EXIT")]
    assert client.post(f"/video/{out_id}/stop").status_code == 200
    db.close()


def test_engine_state_restored_from_db_after_restart(client, db_factory, monkeypatch, stub_models):
    """A fresh process (new engine) must not create a second ENTRY for today"""
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_CAMERA", True)
    db = db_factory()
    student = add_student(db, "S1", encoding=ENCODING)
    camera = add_camera(db, MISSING_SOURCE)
    db.add(models.AttendanceEvent(student_id=student.id, camera_id=camera.id, event_type="ENTRY", confidence=90))
    db.commit()
    camera_id = camera.id

    assert client.post(f"/video/{camera_id}/start").status_code == 200
    time.sleep(1.5)
    db.expire_all()
    assert db.query(models.AttendanceEvent).count() == 1
    assert client.post(f"/video/{camera_id}/stop").status_code == 200
    db.close()


# --- GET /video/{id}/frame status codes ----------------------------------------

class FrameStub:
    """Stand-in for VideoProcessingService.get_latest_frame_jpeg"""

    def __init__(self, result):
        self.result = result

    def get_latest_frame_jpeg(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def frame_session(client):
    """Register a fake processing session for camera 77; removed before client teardown"""
    def install(result):
        video.active_sessions[77] = {"service": FrameStub(result)}
    yield install
    video.active_sessions.pop(77, None)


def test_frame_404_when_no_frame_yet(client, frame_session):
    """Regression: the 404 used to be caught by `except Exception` and returned as
    500 "Failed to get frame: " (empty message with Starlette 0.27)."""
    frame_session(None)
    response = client.get("/video/77/frame")
    assert response.status_code == 404
    assert response.json() == {"detail": "No frame available yet"}


def test_frame_returns_jpeg_bytes(client, frame_session):
    frame_session(b"\xff\xd8\xff\xe0jpeg-bytes")
    response = client.get("/video/77/frame")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"\xff\xd8\xff\xe0jpeg-bytes"


def test_frame_404_without_session(client):
    response = client.get("/video/78/frame")
    assert response.status_code == 404
    assert response.json() == {"detail": "No active video processing session for this camera"}


def test_frame_unexpected_error_is_still_500_with_reason(client, frame_session):
    frame_session(RuntimeError("encoder crashed"))
    response = client.get("/video/77/frame")
    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to get frame: encoder crashed"}
