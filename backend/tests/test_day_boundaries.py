"""
Day-window queries around local midnight, for both stored timestamp formats:
  "YYYY-MM-DD HH:MM:SS"         written by SQLite CURRENT_TIMESTAMP (production rows)
  "YYYY-MM-DD HH:MM:SS.ffffff"  written by SQLAlchemy
Regression: an event at exactly local midnight used to be counted on the previous day.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app import crud
from helpers import add_student

TODAY = date.today()
T0 = crud.local_day_start_utc(TODAY)                       # today 00:00:00 local, as naive UTC
T1 = crud.local_day_start_utc(TODAY + timedelta(days=1))   # tomorrow 00:00:00 local
SECOND = timedelta(seconds=1)

# confidence value labels each instant so we can see which rows were counted
INSTANTS = {
    10: T0 - SECOND,   # yesterday 23:59:59 -> yesterday
    20: T0,            # today 00:00:00     -> today
    30: T0 + SECOND,   # today 00:00:01     -> today
    40: T1 - SECOND,   # today 23:59:59     -> today
    50: T1,            # tomorrow 00:00:00  -> not today
}
TODAY_LABELS = [20, 30, 40]


def stored_text(ts, fmt):
    return ts.strftime("%Y-%m-%d %H:%M:%S") if fmt == "sqlite" else ts.strftime("%Y-%m-%d %H:%M:%S.%f")


@pytest.fixture(params=["sqlite", "sqlalchemy"])
def boundary_events(request, api):
    client, factory = api
    db = factory()
    student_id = add_student(db, "B1").id
    for label, ts in INSTANTS.items():
        db.execute(text("INSERT INTO attendance_events (student_id, event_type, confidence, timestamp) "
                        "VALUES (:s, 'ENTRY', :c, :t)"),
                   {"s": student_id, "c": label, "t": stored_text(ts, request.param)})
    db.commit()
    stored = db.execute(text("SELECT timestamp FROM attendance_events WHERE confidence = 20")).scalar()
    db.close()
    assert ("." in stored) == (request.param == "sqlalchemy")  # the format under test really is stored
    return client, student_id


def test_today_endpoint_uses_exact_local_day(boundary_events):
    client, student_id = boundary_events
    rows = client.get(f"/attendance/student/{student_id}/today").json()
    assert sorted(r["confidence"] for r in rows) == TODAY_LABELS


def test_dashboard_today_counts_use_exact_local_day(boundary_events):
    client, _ = boundary_events
    # "today" stats are open-ended (timestamp >= local midnight), so the 00:00:00 event
    # must be included, 23:59:59 yesterday excluded, and the (future) tomorrow-midnight
    # event is also >= today's start.
    expected = sum(1 for ts in INSTANTS.values() if ts >= T0)
    assert expected == 4
    stats = client.get("/dashboard/stats").json()
    assert stats["today_entries"] == expected
    assert stats["attendance_today"] == expected
    assert stats["present_today"] == 1


@pytest.mark.parametrize("fmt", ["sqlite", "sqlalchemy"])
def test_daily_attendance_assigns_midnight_to_the_new_day(api, fmt):
    """Student A arrives exactly at local midnight, B one second before.
    Before the fix (sqlite format) A was counted yesterday: yesterday=2, today=0."""
    client, factory = api
    db = factory()
    a, b = add_student(db, "A").id, add_student(db, "B").id
    for student_id, ts in ((a, T0), (b, T0 - SECOND)):
        db.execute(text("INSERT INTO attendance_events (student_id, event_type, confidence, timestamp) "
                        "VALUES (:s, 'ENTRY', 90, :t)"), {"s": student_id, "t": stored_text(ts, fmt)})
    db.commit()
    db.close()
    daily = {d["date"]: d for d in client.get("/dashboard/daily/2").json()}
    yesterday, today = daily[str(TODAY - timedelta(days=1))], daily[str(TODAY)]
    assert yesterday["present"] + yesterday["late"] == 1
    assert today["present"] + today["late"] == 1


def test_seconds_only_bound_format():
    assert crud._timestamp_bound(T0) == T0.strftime("%Y-%m-%d %H:%M:%S")
    assert "." not in crud._timestamp_bound(T0)
