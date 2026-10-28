"""
Face enrollment safeguards: image quality gates, wrong-person warnings, listing and
erasing face records. face_recognition is replaced by a stub (no dlib models needed);
the stub returns the face box and encoding each test chooses.
"""
import sys
import types

import cv2
import numpy as np
import pytest

from app import models, services
from helpers import add_student

BOX = (20, 120, 120, 20)  # top, right, bottom, left -> 100 x 100 face


def image_bytes(kind="textured", size=160):
    rng = np.random.default_rng(5)
    if kind == "textured":
        image = rng.integers(60, 200, (size, size, 3), dtype=np.uint8)
    elif kind == "flat":
        image = np.full((size, size, 3), 128, np.uint8)
    elif kind == "dark":
        image = rng.integers(0, 30, (size, size, 3), dtype=np.uint8)
    elif kind == "bright":
        image = rng.integers(235, 256, (size, size, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", image)
    assert ok
    return buf.tobytes()


@pytest.fixture
def face_stub(monkeypatch):
    state = {"box": BOX, "encoding": np.linspace(-0.2, 0.2, 128), "faces": 1}
    fake = types.ModuleType("face_recognition")
    fake.face_locations = lambda image, *a, **k: [state["box"]] * state["faces"]
    fake.face_encodings = lambda image, locations, *a, **k: [state["encoding"]]
    monkeypatch.setitem(sys.modules, "face_recognition", fake)
    return state


@pytest.fixture
def person(api):
    client, factory = api
    db = factory()
    student = add_student(db, "Q1", name="Quinn")
    db.close()
    return client, factory, student.id


def enroll(client, student_id, kind="textured", confirm=False):
    url = f"/face-enrollment/{student_id}" + ("?confirm_warnings=true" if confirm else "")
    return client.post(url, files={"file": ("face.png", image_bytes(kind), "image/png")})


@pytest.mark.parametrize("kind,needle", [("flat", "blurred"), ("dark", "too dark"), ("bright", "over-exposed")])
def test_unusable_photos_are_rejected_with_actionable_reason(person, face_stub, kind, needle):
    client, factory, sid = person
    r = enroll(client, sid, kind)
    assert r.status_code == 422 and needle in r.json()["detail"]
    db = factory()
    assert db.query(models.FaceEmbedding).count() == 0
    db.close()


def test_small_face_rejected(person, face_stub):
    client, _, sid = person
    face_stub["box"] = (10, 50, 50, 10)  # 40 px
    r = enroll(client, sid)
    assert r.status_code == 422 and "too small" in r.json()["detail"]


def test_multiple_or_no_faces_rejected(person, face_stub):
    client, _, sid = person
    face_stub["faces"] = 2
    assert "Multiple faces" in enroll(client, sid).json()["detail"]
    face_stub["faces"] = 0
    assert "No face" in enroll(client, sid).json()["detail"]


def test_good_photo_enrolls_and_reports_quality(person, face_stub):
    client, _, sid = person
    r = enroll(client, sid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["warnings"] == [] and body["quality"]["face_size"] == 100.0
    assert "embedding_data" not in body


def test_face_resembling_another_person_needs_confirmation(person, face_stub):
    client, factory, sid = person
    db = factory()
    other = add_student(db, "R2", name="Riya")
    db.add(models.FaceEmbedding(student_id=other.id, embedding_data=(face_stub["encoding"] + 0.01).tobytes()))
    db.commit()
    db.close()

    r = enroll(client, sid)
    assert r.status_code == 409 and "Riya (R2)" in r.json()["detail"]
    r = enroll(client, sid, confirm=True)
    assert r.status_code == 200 and "Riya" in r.json()["warnings"][0]


def test_list_and_erase_face_records(person, face_stub):
    client, factory, sid = person
    embedding_id = enroll(client, sid).json()["id"]
    listed = client.get(f"/face-enrollment/{sid}").json()
    assert [f["id"] for f in listed] == [embedding_id] and set(listed[0]) == {"id", "is_active", "created_at"}

    version = services.get_known_faces_version()
    assert client.delete(f"/face-enrollment/{sid}/{embedding_id}").status_code == 200
    assert services.get_known_faces_version() == version + 1
    assert client.get(f"/face-enrollment/{sid}").json() == []
    assert client.delete(f"/face-enrollment/{sid}/{embedding_id}").status_code == 404
    db = factory()
    assert db.query(models.AuditLog).filter_by(action="face.delete").count() == 1
    db.close()
