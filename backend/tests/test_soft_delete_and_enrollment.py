"""
Soft delete of students/cameras (history preserved), DB-backed course
enrollments, and face-enrollment persistence.
"""
import sys
import types
from datetime import time, date

import cv2
import numpy as np
import pytest
from sqlalchemy.exc import IntegrityError

from app import models, services
from app.routers import video
from helpers import add_camera, add_event, add_student, utc_naive


@pytest.fixture
def seeded(api):
    client, factory = api
    db = factory()
    student = add_student(db, "S1")
    camera = add_camera(db)
    db.add(models.FaceEmbedding(student_id=student.id, embedding_data=np.zeros(128).tobytes()))
    db.commit()
    event = add_event(db, student.id, "ENTRY", utc_naive(date.today(), time(8, 0)), camera.id)
    ids = {"student": student.id, "camera": camera.id, "event": event.id}
    db.close()
    return client, factory, ids


# --- students -----------------------------------------------------------------

def test_delete_student_is_soft_and_keeps_history(seeded):
    client, factory, ids = seeded
    version = services.get_known_faces_version()

    r = client.delete(f"/students/{ids['student']}")
    assert r.status_code == 200 and r.json() == {"message": "Student deleted successfully"}
    assert services.get_known_faces_version() == version + 1  # running workers reload faces

    db = factory()
    student = db.get(models.Student, ids["student"])
    assert student is not None and student.deleted_at is not None and not student.is_active
    assert all(not e.is_active for e in student.face_embeddings)
    assert db.get(models.AttendanceEvent, ids["event"]) is not None
    db.close()

    assert client.get("/students/").json() == []
    archived = client.get("/students/?include_deleted=true").json()
    assert [s["id"] for s in archived] == [ids["student"]] and archived[0]["deleted_at"]
    assert client.get(f"/students/{ids['student']}").status_code == 404
    assert client.put(f"/students/{ids['student']}", json={"student_id": "S1", "name": "X"}).status_code == 404
    assert client.delete(f"/students/{ids['student']}").status_code == 404
    assert client.get(f"/attendance/{ids['event']}").status_code == 200


def test_deleted_student_id_cannot_be_reused(seeded):
    client, _, ids = seeded
    client.delete(f"/students/{ids['student']}")
    r = client.post("/students/", json={"student_id": "S1", "name": "Someone else"})
    assert r.status_code == 400 and "deleted student" in r.json()["detail"]


def test_deleted_student_not_loaded_for_recognition(seeded):
    client, factory, ids = seeded
    client.delete(f"/students/{ids['student']}")
    svc = services.VideoProcessingService(video_source=0)
    db = factory()
    svc.load_known_faces_from_db(db)
    db.close()
    assert svc.face_detector.known_face_student_ids == []


def test_orm_hard_delete_cannot_silently_drop_history(seeded):
    """Defence in depth: the ORM no longer cascades deletes to attendance_events"""
    _, factory, ids = seeded
    db = factory()
    db.delete(db.get(models.Student, ids["student"]))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.get(models.AttendanceEvent, ids["event"]) is not None
    db.close()


# --- cameras ------------------------------------------------------------------

def test_delete_camera_is_soft_and_keeps_history(seeded):
    client, factory, ids = seeded
    assert client.delete(f"/cameras/{ids['camera']}").status_code == 200
    db = factory()
    camera = db.get(models.Camera, ids["camera"])
    assert camera.deleted_at is not None and not camera.is_active
    assert db.get(models.AttendanceEvent, ids["event"]).camera_id == ids["camera"]
    db.close()
    assert client.get("/cameras/").json() == []
    assert len(client.get("/cameras/?include_deleted=true").json()) == 1
    assert client.get(f"/cameras/{ids['camera']}").status_code == 404
    assert client.post(f"/cameras/{ids['camera']}/toggle").status_code == 404
    assert client.post(f"/video/{ids['camera']}/start").status_code == 404
    # Recent attendance still shows the deleted camera's name
    assert client.get("/attendance/recent").json()[0]["camera_name"] == "Door"


def test_cannot_delete_camera_while_processing(seeded):
    client, _, ids = seeded
    video.active_sessions[ids["camera"]] = {"service": None}
    try:
        r = client.delete(f"/cameras/{ids['camera']}")
        assert r.status_code == 409
    finally:
        video.active_sessions.pop(ids["camera"], None)


# --- course enrollments (/enrollment) ------------------------------------------

def enrollment_payload(student_id, **extra):
    payload = {"student_id": student_id, "course_code": "CS101", "course_name": "Intro to CS",
               "semester": "Fall 2026", "section": "A", "enrolled_date": "2026-08-25"}
    payload.update(extra)
    return payload


def test_course_enrollment_crud_is_persisted(seeded):
    client, factory, ids = seeded
    r = client.post("/enrollment/", json=enrollment_payload(ids["student"]))
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["id"] and created["created_at"] and created["is_active"] is True

    # Persisted in the database (survives the in-memory process state)
    db = factory()
    row = db.get(models.Enrollment, created["id"])
    assert row.course_code == "CS101" and row.enrolled_date == date(2026, 8, 25)
    db.close()

    assert client.get(f"/enrollment/{created['id']}").json()["course_name"] == "Intro to CS"
    assert [e["id"] for e in client.get("/enrollment/?course_code=CS101").json()] == [created["id"]]
    assert client.get("/enrollment/?semester=Spring").json() == []

    r = client.put(f"/enrollment/{created['id']}", json=enrollment_payload(ids["student"], section="B"))
    assert r.status_code == 200 and r.json()["section"] == "B" and r.json()["updated_at"]

    assert client.delete(f"/enrollment/{created['id']}").json() == {"message": "Enrollment deleted successfully"}
    assert client.get(f"/enrollment/student/{ids['student']}").json() == []
    assert client.get("/enrollment/?is_active=false").json()[0]["id"] == created["id"]


def test_course_enrollment_validation(seeded):
    client, _, ids = seeded
    assert client.post("/enrollment/", json=enrollment_payload(ids["student"])).status_code == 200
    dup = client.post("/enrollment/", json=enrollment_payload(ids["student"]))
    assert dup.status_code == 400 and "already enrolled" in dup.json()["detail"]
    assert client.post("/enrollment/", json=enrollment_payload(0)).status_code == 400
    assert client.post("/enrollment/", json=enrollment_payload(9999)).status_code == 404
    assert client.get("/enrollment/9999").status_code == 404


# --- face enrollment (/face-enrollment) ------------------------------------------

def png_bytes():
    # Textured (not flat) so it passes the enrollment sharpness check; see test_enrollment_quality.py
    image = np.random.default_rng(3).integers(60, 200, (120, 120, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", image)
    assert ok
    return buf.tobytes()


@pytest.fixture
def fake_face_recognition(monkeypatch):
    """Stand-in for face_recognition so enrollment can be tested without dlib models"""
    encoding = np.linspace(-0.2, 0.2, 128)
    fake = types.ModuleType("face_recognition")
    fake.face_locations = lambda image, *a, **k: [(10, 90, 90, 10)]
    fake.face_encodings = lambda image, locations, *a, **k: [encoding]
    monkeypatch.setitem(sys.modules, "face_recognition", fake)
    return encoding


def test_face_enrollment_persists_embedding_and_notifies_workers(seeded, fake_face_recognition):
    client, factory, ids = seeded
    version = services.get_known_faces_version()
    # The seeded student already has an all-zero placeholder encoding, which the new face
    # does not resemble: the wrong-person safeguard (test_enrollment_quality.py) needs an
    # explicit confirmation here.
    r = client.post(f"/face-enrollment/{ids['student']}?confirm_warnings=true",
                    files={"file": ("face.png", png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["warnings"]  # the mismatch was reported, not hidden
    assert r.json()["student_id"] == ids["student"]
    assert services.get_known_faces_version() == version + 1

    db = factory()
    stored = db.get(models.FaceEmbedding, r.json()["id"])
    np.testing.assert_array_equal(np.frombuffer(stored.embedding_data, dtype=np.float64), fake_face_recognition)
    svc = services.VideoProcessingService(video_source=0)
    svc.load_known_faces_from_db(db)
    assert ids["student"] in svc.face_detector.known_face_student_ids
    db.close()


def test_face_enrollment_rejected_for_deleted_student(seeded, fake_face_recognition):
    client, _, ids = seeded
    client.delete(f"/students/{ids['student']}")
    r = client.post(f"/face-enrollment/{ids['student']}", files={"file": ("face.png", png_bytes(), "image/png")})
    assert r.status_code == 404


def test_face_enrollment_503_when_face_recognition_unavailable(seeded, monkeypatch):
    client, _, ids = seeded
    monkeypatch.setitem(sys.modules, "face_recognition", None)  # import raises ImportError
    r = client.post(f"/face-enrollment/{ids['student']}", files={"file": ("face.png", png_bytes(), "image/png")})
    assert r.status_code == 503
