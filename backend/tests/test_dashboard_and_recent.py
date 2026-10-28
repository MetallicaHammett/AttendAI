"""
Dashboard statistics from real DB queries, and GET /attendance/recent.
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.config import settings
from helpers import add_camera, add_event, add_student, utc_naive

TODAY = date.today()


def days_ago(n):
    return TODAY - timedelta(days=n)


@pytest.fixture
def school(api):
    """3 active students + 1 inactive + 1 deleted, and a camera"""
    client, factory = api
    db = factory()
    s = [add_student(db, f"S{i}") for i in range(1, 4)]
    add_student(db, "S-inactive", active=False)
    deleted = add_student(db, "S-deleted")
    deleted.deleted_at, deleted.is_active = datetime.now(timezone.utc), False
    db.commit()
    cam = add_camera(db)
    ids = {"students": [x.id for x in s], "deleted": deleted.id, "camera": cam.id}
    db.close()
    return client, factory, ids


def test_dashboard_root_no_longer_500(school):
    client, _, _ = school
    r = client.get("/dashboard/")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"stats", "daily_attendance", "trend"}
    assert len(body["daily_attendance"]) == 7


def test_stats_today_counts_and_excludes_deleted(school):
    client, factory, ids = school
    s1, s2, _ = ids["students"]
    db = factory()
    add_event(db, s1, "ENTRY", utc_naive(TODAY, time(8, 0)), ids["camera"])
    add_event(db, s1, "EXIT", utc_naive(TODAY, time(12, 0)), ids["camera"])
    add_event(db, s2, "ENTRY", utc_naive(TODAY, time(8, 5)), ids["camera"])
    add_event(db, s2, "ENTRY", utc_naive(days_ago(1), time(8, 5)), ids["camera"])  # yesterday
    db.close()

    stats = client.get("/dashboard/stats").json()
    assert stats["total_students"] == 4      # deleted student excluded
    assert stats["active_students"] == 3
    assert stats["total_cameras"] == 1
    assert stats["attendance_today"] == 3    # events today
    assert stats["today_entries"] == 2 and stats["today_exits"] == 1
    assert stats["present_today"] == 2       # distinct students
    assert stats["estimated_present"] == 1


def test_daily_attendance_uses_real_events(school, monkeypatch):
    monkeypatch.setattr(settings, "ATTENDANCE_LATE_AFTER", "09:00")
    client, factory, ids = school
    s1, s2, s3 = ids["students"]
    db = factory()
    # Today: s1 on time (+ a later re-entry that must not count twice), s2 late
    add_event(db, s1, "ENTRY", utc_naive(TODAY, time(8, 50)))
    add_event(db, s1, "ENTRY", utc_naive(TODAY, time(13, 0)))
    add_event(db, s2, "ENTRY", utc_naive(TODAY, time(9, 15)))
    # 2 days ago: all three on time; an EXIT does not count as attendance
    for sid in (s1, s2, s3):
        add_event(db, sid, "ENTRY", utc_naive(days_ago(2), time(8, 0)))
        add_event(db, sid, "EXIT", utc_naive(days_ago(2), time(15, 0)))
    # Deleted student's history still counts on that day
    add_event(db, ids["deleted"], "ENTRY", utc_naive(days_ago(2), time(8, 0)))
    # 10 days ago: outside the 7-day window
    add_event(db, s3, "ENTRY", utc_naive(days_ago(10), time(8, 0)))
    db.close()

    r = client.get("/dashboard/daily/7")
    assert r.status_code == 200
    daily = r.json()
    assert [d["date"] for d in daily] == [str(days_ago(n)) for n in range(6, -1, -1)]
    by_day = {d["date"]: d for d in daily}
    assert by_day[str(TODAY)] == {"date": str(TODAY), "present": 1, "late": 1, "absent": 1, "total": 3}
    assert by_day[str(days_ago(2))] == {"date": str(days_ago(2)), "present": 4, "late": 0, "absent": 0, "total": 4}
    assert by_day[str(days_ago(1))] == {"date": str(days_ago(1)), "present": 0, "late": 0, "absent": 3, "total": 3}


def test_daily_attendance_without_late_setting_counts_everyone_present(school, monkeypatch):
    monkeypatch.setattr(settings, "ATTENDANCE_LATE_AFTER", None)
    client, factory, ids = school
    db = factory()
    add_event(db, ids["students"][0], "ENTRY", utc_naive(TODAY, time(11, 0)))
    db.close()
    today = client.get("/dashboard/daily/1").json()[0]
    assert today["present"] == 1 and today["late"] == 0


@pytest.mark.parametrize("days", [0, 367])
def test_daily_attendance_rejects_bad_range(school, days):
    client, _, _ = school
    assert client.get(f"/dashboard/daily/{days}").status_code == 422


def test_trend_compares_last_week_with_previous_week(school):
    client, factory, ids = school
    s1, s2, _ = ids["students"]
    db = factory()
    for n in range(8, 15):     # previous week: 1 attendee per day
        add_event(db, s1, "ENTRY", utc_naive(days_ago(n), time(8, 0)))
    for n in range(1, 8):      # last week: 2 attendees per day
        add_event(db, s1, "ENTRY", utc_naive(days_ago(n), time(8, 0)))
        add_event(db, s2, "ENTRY", utc_naive(days_ago(n), time(8, 0)))
    add_event(db, s1, "ENTRY", utc_naive(TODAY, time(8, 0)))  # today (incomplete) is ignored
    db.close()
    assert client.get("/dashboard/trend").json() == {"trend": "up", "change_percentage": 100.0, "period_days": 14}


def test_trend_stable_without_data(school):
    client, _, _ = school
    assert client.get("/dashboard/trend").json() == {"trend": "stable", "change_percentage": 0.0, "period_days": 14}


# --- /attendance/recent -------------------------------------------------------

def test_recent_attendance_returns_names_newest_first(school):
    client, factory, ids = school
    s1, s2, _ = ids["students"]
    db = factory()
    e1 = add_event(db, s1, "ENTRY", utc_naive(TODAY, time(8, 0)), ids["camera"], confidence=91).id
    e2 = add_event(db, s2, "ENTRY", utc_naive(TODAY, time(8, 30)), None, confidence=88).id
    e3 = add_event(db, ids["deleted"], "EXIT", utc_naive(TODAY, time(9, 0)), ids["camera"]).id
    db.close()

    r = client.get("/attendance/recent?limit=10")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["id"] for row in rows] == [e3, e2, e1]
    assert rows[2] == {
        "id": e1, "student_name": "Student S1", "student_id": "S1", "event_type": "ENTRY",
        "timestamp": rows[2]["timestamp"], "confidence": 91, "camera_name": "Door",
        "student_db_id": s1, "camera_id": ids["camera"],
        "source": "camera", "person_type": "student",  # added with manual corrections / staff support
    }
    assert rows[1]["camera_name"] is None and rows[1]["camera_id"] is None
    assert rows[0]["student_id"] == "S-deleted"  # history of deleted students preserved
    # Timestamps are timezone-aware UTC and round-trip to the stored instant
    parsed = datetime.fromisoformat(rows[2]["timestamp"].replace("Z", "+00:00"))
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.replace(tzinfo=None) == utc_naive(TODAY, time(8, 0))


def test_recent_attendance_limit_camera_filter_and_trailing_slash(school):
    client, factory, ids = school
    db = factory()
    for i, sid in enumerate(ids["students"]):
        add_event(db, sid, "ENTRY", utc_naive(TODAY, time(8, i)), ids["camera"] if i else None)
    db.close()
    assert len(client.get("/attendance/recent?limit=2").json()) == 2
    assert {r["camera_id"] for r in client.get(f"/attendance/recent?camera_id={ids['camera']}").json()} == {ids["camera"]}
    # The frontend calls /attendance/recent/?limit=100
    r = client.get("/attendance/recent/?limit=100")
    assert r.status_code == 200 and len(r.json()) == 3
    assert client.get("/attendance/recent?limit=0").status_code == 422


def test_attendance_by_id_still_works(school):
    client, factory, ids = school
    db = factory()
    event_id = add_event(db, ids["students"][0], "ENTRY", utc_naive(TODAY, time(8, 0))).id
    db.close()
    r = client.get(f"/attendance/{event_id}")
    assert r.status_code == 200 and r.json()["id"] == event_id
