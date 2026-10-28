"""
MOCKED INTEGRATION TEST - the whole application path with only the computer-vision
models replaced:

  login (real bcrypt + JWT) -> create classroom, cameras, timetable and student via the API
  -> enroll a face (face_recognition stubbed) -> start the IN camera (synthetic frames;
  person/face detection stubbed to "see" the enrolled student and an unregistered visitor)
  -> the real worker thread, matcher, AttendanceEngine, UnknownOccupancyTracker and
  recorder persist the ENTRY -> dashboard, live view, period attendance, sessions and
  CSV report all agree -> stop the camera and its coverage record is closed.

It proves the integration and data consistency. It does NOT prove recognition accuracy
or camera connectivity: no real face or camera is involved.
"""
import sys
import time
import types
from datetime import datetime, timedelta

import cv2
import numpy as np
import pytest

from app import models, services
from app.config import settings
from app.security import get_password_hash
from helpers import ASGIClient

STUDENT_FACE = np.random.default_rng(21).normal(0, 0.09, 128)
VISITOR_FACE = np.random.default_rng(22).normal(0, 0.09, 128)


def wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


@pytest.fixture
def environment(api, monkeypatch, tmp_path):
    _, factory = api
    db = factory()
    db.add(models.User(email="ops@example.com", username="ops", hashed_password=get_password_hash("ops-password-1"),
                       role="admin"))
    db.commit()
    db.close()

    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "entrance.mp4").write_bytes(b"not a real video")  # cannot be opened -> synthetic frames
    monkeypatch.setattr(settings, "VIDEO_FILES_DIR", str(videos))
    monkeypatch.setattr(settings, "ENABLE_SYNTHETIC_CAMERA", True)
    monkeypatch.setattr(settings, "CAMERA_AUTO_RECONNECT", False)
    monkeypatch.setattr(settings, "ATTENDANCE_MIN_CONSECUTIVE_FRAMES", 3)
    monkeypatch.setattr(settings, "FACE_RELOAD_INTERVAL_SECONDS", 3600.0)

    # Face enrollment stub: the uploaded photo "contains" STUDENT_FACE
    fake = types.ModuleType("face_recognition")
    fake.face_locations = lambda image, *a, **k: [(10, 110, 110, 10)]
    fake.face_encodings = lambda image, locations, *a, **k: [STUDENT_FACE]
    monkeypatch.setitem(sys.modules, "face_recognition", fake)

    # Camera stubs: every frame shows the student and one unregistered visitor
    seen = [STUDENT_FACE, VISITOR_FACE]
    monkeypatch.setattr(services.PersonDetectionService, "detect_persons", lambda self, frame: services.DetectionResult(
        [(0, 0, 100, 200), (120, 0, 220, 200)], [0.9, 0.9], services.DetectionStatus.SUCCESS, frame.shape))
    monkeypatch.setattr(services.FaceDetectionService, "detect_faces", lambda self, frame, boxes: services.FaceResult(
        [(10, 60, 60, 10), (10, 180, 60, 130)], [e.copy() for e in seen], services.DetectionStatus.SUCCESS, boxes))
    monkeypatch.setattr(services.TrackingService, "update_tracks",
                        lambda self, frame, dets: services.TrackingResult([], services.DetectionStatus.NO_PERSON))

    from app.main import app
    anonymous = ASGIClient(app)
    token = anonymous.post("/auth/login", data={"username": "ops", "password": "ops-password-1"}).json()["access_token"]
    client = ASGIClient(app, headers={"Authorization": f"Bearer {token}"})
    yield client, factory
    from app.routers import video
    for camera_id in list(video.active_sessions):
        client.post(f"/video/{camera_id}/stop")


def png():
    ok, buf = cv2.imencode(".png", np.random.default_rng(4).integers(60, 200, (140, 140, 3), dtype=np.uint8))
    return buf.tobytes()


def test_full_attendance_flow_with_mocked_vision(environment):
    client, factory = environment
    assert client.get("/auth/me").json()["role"] == "admin"

    room = client.post("/timetable/classrooms", json={"name": "Lab 1"}).json()
    cam_in = client.post("/cameras/", json={"name": "Lab 1 IN", "source": "entrance.mp4", "source_type": "video",
                                            "camera_type": "IN", "classroom_id": room["id"]})
    assert cam_in.status_code == 200, cam_in.text
    cam_in = cam_in.json()
    student = client.post("/students/", json={"student_id": "21CSE042", "name": "Meera", "class_name": "CSE-B"}).json()

    now = datetime.now()
    start = max(now - timedelta(minutes=20), now.replace(hour=0, minute=0, second=0, microsecond=0))
    end = min(now + timedelta(minutes=40), now.replace(hour=23, minute=59, second=0, microsecond=0))
    period = client.post("/timetable/periods", json={
        "classroom_id": room["id"], "class_name": "CSE-B", "course_name": "Networks", "day_of_week": now.weekday(),
        "start_time": start.strftime("%H:%M"), "end_time": end.strftime("%H:%M"), "grace_minutes": 30})
    assert period.status_code == 200, period.text

    enrolled = client.post(f"/face-enrollment/{student['id']}", files={"file": ("meera.png", png(), "image/png")})
    assert enrolled.status_code == 200, enrolled.text

    started = client.post(f"/video/{cam_in['id']}/start")
    assert started.status_code == 200, started.text
    assert started.json()["feed_kind"] == "synthetic"  # labelled honestly: the file could not be opened

    def entries():
        db = factory()
        try:
            return db.query(models.AttendanceEvent).filter_by(student_id=student["id"], event_type="ENTRY").all()
        finally:
            db.close()

    assert wait_for(lambda: len(entries()) == 1), "the worker never recorded the ENTRY"
    assert wait_for(lambda: client.get("/video/live").json()["occupancy"]["unknown_inside_count"] == 1)
    time.sleep(1.0)
    assert len(entries()) == 1  # continued sightings do not duplicate it

    live = client.get("/video/live").json()
    assert live["sessions"][0]["service_stats"]["feed_kind"] == "synthetic"
    kinds = {o["kind"] for o in live["observations"]}
    assert {"known", "unknown"} <= kinds
    assert any(o["student_name"] == "Meera" and o["decision"] == "ENTRY" for o in live["observations"])

    stats = client.get("/dashboard/stats").json()
    assert stats["present_today"] == 1 and stats["known_inside_count"] == 1 and stats["students_inside"] == 1
    assert stats["unknown_inside_count"] == 1 and stats["cameras_processing"] == 1
    assert stats["classroom_occupancy"][0] == {"classroom_id": room["id"], "name": "Lab 1", "known_inside": 1}

    attendance = client.get(f"/timetable/periods/{period.json()['id']}/attendance?date={now.date()}").json()
    # The camera ran for seconds of a longer class: not "covered", so nobody unseen could be
    # called absent - but the student it did recognise counts
    assert attendance["camera_coverage"] is False
    assert attendance["students"][0]["status"] in ("PRESENT", "LATE")

    recent = client.get("/attendance/recent").json()
    assert recent[0]["student_id"] == "21CSE042" and recent[0]["camera_name"] == "Lab 1 IN"
    csv_text = client.get(f"/reports/sessions.csv?start_date={now.date()}&end_date={now.date()}").content.decode("utf-8-sig")
    assert "21CSE042" in csv_text and "Lab 1 IN" in csv_text

    assert client.post(f"/video/{cam_in['id']}/stop").status_code == 200
    db = factory()
    run = db.query(models.CameraRun).filter_by(camera_id=cam_in["id"]).one()
    assert run.stopped_at is not None and run.stop_reason == "stopped by operator" and run.feed_kind == "synthetic"
    actions = [a.action for a in db.query(models.AuditLog).order_by(models.AuditLog.id)]
    db.close()
    assert actions[:2] == ["classroom.create", "camera.create"] and "video.start" in actions and "video.stop" in actions
