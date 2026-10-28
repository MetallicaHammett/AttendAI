"""
Phase 2.1: attendance references must point at live students/cameras, the
database refuses to drop attendance history, and every API timestamp is UTC.
"""
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import crud, models, services
from app.routers import video
from helpers import add_camera, add_event, add_student, utc_naive

TODAY = date.today()


@pytest.fixture
def world(api):
    """Live + deleted student, live + deleted camera"""
    client, factory = api
    db = factory()
    live_s, gone_s = add_student(db, "LIVE"), add_student(db, "GONE")
    live_c, gone_c = add_camera(db, "Live cam"), add_camera(db, "Gone cam")
    ids = {"live_s": live_s.id, "gone_s": gone_s.id, "live_c": live_c.id, "gone_c": gone_c.id}
    crud.delete_student(db, gone_s.id)
    crud.delete_camera(db, gone_c.id)
    db.close()
    return client, factory, ids


def event_count(factory):
    db = factory()
    try:
        return db.query(models.AttendanceEvent).count()
    finally:
        db.close()


# --- 1. live references only ------------------------------------------------------

def decision(student_id):
    return services.AttendanceDecision(student_id=student_id, confidence=90.0, event_type="ENTRY",
                                       timestamp=0.0, should_record=True, reasoning="")


@pytest.mark.parametrize("student_key,camera_key,expected", [
    ("live_s", "live_c", None),
    ("gone_s", "live_c", "deleted student"),
    ("missing", "live_c", "deleted student"),
    ("live_s", "gone_c", "deleted camera"),
    ("live_s", "missing", "deleted camera"),
])
def test_recorder_only_accepts_live_student_and_camera(world, student_key, camera_key, expected):
    _, factory, ids = world
    ids = {**ids, "missing": 9999}
    db = factory()
    record = video.make_event_recorder(db, ids[camera_key], {})
    if expected is None:
        record(decision(ids[student_key]))
        assert event_count(factory) == 1
    else:
        with pytest.raises(ValueError, match=expected):
            record(decision(ids[student_key]))
        assert event_count(factory) == 0
    db.close()


def test_rejected_recording_rolls_back_engine_state(world):
    """Through the shared engine: a rejected write leaves the student OUTSIDE"""
    _, factory, ids = world
    db = factory()
    engine = services.AttendanceEngine(min_consecutive_frames=1)
    record = video.make_event_recorder(db, ids["live_c"], {})
    d = engine.process_recognition(ids["gone_s"], 95.0, 1_000_000.0, "IN", camera_id=ids["live_c"], persist=record)
    assert d.should_record is False and "deleted student" in d.reasoning
    assert engine.inside_count(1_000_000.0) == 0 and event_count(factory) == 0
    db.close()


def payload(student_id, camera_id, event_type="ENTRY"):
    return {"student_id": student_id, "camera_id": camera_id, "event_type": event_type, "confidence": 80}


@pytest.mark.parametrize("student_key,camera_key,needle", [
    ("gone_s", "live_c", "Student"),
    ("missing", "live_c", "Student"),
    ("live_s", "gone_c", "Camera"),
    ("live_s", "missing", "Camera"),
])
def test_post_attendance_rejects_dead_references(world, student_key, camera_key, needle):
    client, factory, ids = world
    ids = {**ids, "missing": 9999}
    r = client.post("/attendance/", json=payload(ids[student_key], ids[camera_key]))
    assert r.status_code == 400 and needle in r.json()["detail"]
    assert event_count(factory) == 0


def test_post_attendance_accepts_live_references_and_null_camera(world):
    client, factory, ids = world
    assert client.post("/attendance/", json=payload(ids["live_s"], ids["live_c"])).status_code == 200
    assert client.post("/attendance/", json=payload(ids["live_s"], None)).status_code == 200
    assert event_count(factory) == 2


def test_put_attendance_rejects_dead_references(world):
    client, factory, ids = world
    created = client.post("/attendance/", json=payload(ids["live_s"], ids["live_c"])).json()
    for body in (payload(ids["gone_s"], ids["live_c"]), payload(ids["live_s"], ids["gone_c"]), payload(9999, None)):
        assert client.put(f"/attendance/{created['id']}", json=body).status_code == 400
    ok = client.put(f"/attendance/{created['id']}", json=payload(ids["live_s"], None, "EXIT"))
    assert ok.status_code == 200 and ok.json()["event_type"] == "EXIT" and ok.json()["camera_id"] is None


def test_dashboard_and_recent_agree_once_orphans_are_impossible(world):
    client, factory, ids = world
    client.post("/attendance/", json=payload(ids["live_s"], ids["live_c"]))
    client.post("/attendance/", json=payload(9999, None))  # rejected
    stats = client.get("/dashboard/stats").json()
    assert stats["today_entries"] == len(client.get("/attendance/recent").json()) == 1


# --- 2. database refuses to drop history -------------------------------------------

def test_foreign_keys_enforced_on_app_engine():
    from app.database import engine
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_orm_hard_delete_of_camera_with_history_fails(world):
    """Regression: the ORM used to null attendance_events.camera_id silently"""
    _, factory, ids = world
    db = factory()
    event_id = add_event(db, ids["live_s"], "ENTRY", camera_id=ids["live_c"]).id
    db.delete(db.get(models.Camera, ids["live_c"]))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()
    db = factory()
    assert db.get(models.AttendanceEvent, event_id).camera_id == ids["live_c"]
    assert db.get(models.Camera, ids["live_c"]) is not None
    db.close()


def test_raw_sql_delete_of_student_with_history_fails(world):
    _, factory, ids = world
    db = factory()
    add_event(db, ids["live_s"], "ENTRY")
    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM students WHERE id = :id"), {"id": ids["live_s"]})
        db.commit()
    db.rollback()
    assert db.get(models.Student, ids["live_s"]) is not None
    db.close()


def test_hard_delete_without_history_still_allowed(world):
    _, factory, ids = world
    db = factory()
    db.delete(db.get(models.Camera, ids["live_c"]))
    db.commit()
    assert db.get(models.Camera, ids["live_c"]) is None
    db.close()


def test_orphan_insert_rejected_by_database(world):
    _, factory, _ = world
    db = factory()
    db.add(models.AttendanceEvent(student_id=9999, event_type="ENTRY", confidence=90))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()


# --- 3. UTC timestamps everywhere ----------------------------------------------------

def is_utc(value: str) -> bool:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.utcoffset() == timedelta(0)


def test_every_timestamp_in_responses_is_utc(world):
    client, factory, ids = world
    db = factory()
    db.add(models.FaceEmbedding(student_id=ids["live_s"], embedding_data=np.zeros(128).tobytes()))
    db.commit()
    db.close()
    event = client.post("/attendance/", json=payload(ids["live_s"], ids["live_c"])).json()
    client.post("/enrollment/", json={"student_id": ids["live_s"], "course_code": "CS101",
                                      "course_name": "CS", "semester": "F26"})
    student = client.put(f"/students/{ids['live_s']}", json={"student_id": "LIVE", "name": "Renamed"}).json()

    responses = {
        "POST /attendance/": [event],
        "GET /attendance/": client.get("/attendance/").json(),
        "GET /attendance/{id}": [client.get(f"/attendance/{event['id']}").json()],
        "GET /attendance/student/{id}/today": client.get(f"/attendance/student/{ids['live_s']}/today").json(),
        "GET /attendance/recent": client.get("/attendance/recent").json(),
        "GET /students/?include_deleted": client.get("/students/?include_deleted=true").json(),
        "PUT /students/{id}": [student],
        "GET /cameras/?include_deleted": client.get("/cameras/?include_deleted=true").json(),
        "GET /enrollment/": client.get("/enrollment/").json(),
        "GET /auth/me": [client.get("/auth/me").json()],
    }
    checked = 0
    for name, rows in responses.items():
        assert rows, f"{name} returned nothing"
        for row in rows:
            for field in ("timestamp", "created_at", "updated_at", "deleted_at"):
                if row.get(field) is not None:
                    assert is_utc(row[field]), f"{name}.{field} = {row[field]!r} is not UTC-aware"
                    checked += 1
    # deleted_at (students + cameras), updated_at (renamed student) and timestamp fields were all present
    assert checked >= 15


def test_utc_serialisation_preserves_the_stored_instant(world):
    client, factory, ids = world
    stored = utc_naive(TODAY, time(8, 15))
    db = factory()
    event_id = add_event(db, ids["live_s"], "ENTRY", stored).id
    db.close()
    for url in (f"/attendance/{event_id}", "/attendance/recent"):
        body = client.get(url).json()
        row = body[0] if isinstance(body, list) else body
        assert datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) == stored.replace(tzinfo=timezone.utc)
