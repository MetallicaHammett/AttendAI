"""
Timetable, period attendance (statuses, exceptions, camera coverage, overrides), daily
and monthly summaries, presence sessions, manual events and report exports.
All data is created in an isolated SQLite file per test.
"""
from datetime import date, datetime, time, timedelta

import pytest

from app import attendance_logic, models
from helpers import add_camera, add_event, add_student, utc_naive

# A fixed Monday well in the past, so every period on it is "completed"
DAY = date.today() - timedelta(days=date.today().weekday() + 14)
MONDAY = 0


def at(hh, mm, day=DAY):
    """Local wall-clock time on `day`, as the naive UTC the database stores"""
    return utc_naive(day, time(hh, mm))


def add_run(db, camera_id, start, end):
    db.add(models.CameraRun(camera_id=camera_id, started_at=start, stopped_at=end, feed_kind="live"))
    db.commit()


@pytest.fixture
def school(api):
    """Classroom with an IN and OUT camera, class CSE-A (3 students + 1 staff with the
    same class name), and two Monday periods 09:00-10:00 and 10:15-11:15"""
    client, factory = api
    db = factory()
    room = models.Classroom(name="Room 101", is_active=True)
    db.add(room)
    db.commit()
    cam_in, cam_out = add_camera(db, "Door IN", "IN"), add_camera(db, "Door OUT", "OUT")
    cam_in.classroom_id = cam_out.classroom_id = room.id
    students = {}
    for sid in ("A1", "A2", "A3"):
        s = add_student(db, sid)
        s.class_name = "CSE-A"
        students[sid] = s.id
    staff = add_student(db, "T1", name="Teacher")
    staff.class_name, staff.person_type = "CSE-A", "staff"
    p1 = models.TimetablePeriod(classroom_id=room.id, class_name="CSE-A", course_name="Maths", day_of_week=MONDAY,
                                start_time=time(9, 0), end_time=time(10, 0), grace_minutes=10, is_active=True)
    p2 = models.TimetablePeriod(classroom_id=room.id, class_name="CSE-A", course_name="Physics", day_of_week=MONDAY,
                                start_time=time(10, 15), end_time=time(11, 15), grace_minutes=10, is_active=True)
    # The timetable already existed on DAY (periods only count from the day they were added)
    p1.created_at = p2.created_at = at(8, 0, DAY - timedelta(days=30))
    db.add_all([p1, p2])
    db.commit()
    ids = {"room": room.id, "in": cam_in.id, "out": cam_out.id, "p1": p1.id, "p2": p2.id, "staff": staff.id, **students}
    db.close()
    return client, factory, ids


def statuses(result):
    return {s["student_id"]: s["status"] for s in result["students"]}


def period(client, period_id, day=DAY):
    r = client.get(f"/timetable/periods/{period_id}/attendance?date={day}")
    assert r.status_code == 200, r.text
    return r.json()


def test_present_late_absent_with_camera_coverage(school):
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(8, 55), ids["in"])   # on time
    add_event(db, ids["A2"], "ENTRY", at(9, 25), ids["in"])   # after 09:10 grace -> late
    add_event(db, ids["staff"], "ENTRY", at(8, 50), ids["in"])  # staff never on the roster
    db.close()

    result = period(client, ids["p1"])
    assert statuses(result) == {"A1": "PRESENT", "A2": "LATE", "A3": "ABSENT"}
    assert result["roster_size"] == 3 and result["camera_coverage"] is True
    assert result["counts"]["PRESENT"] == 1 and result["counts"]["LATE"] == 1 and result["counts"]["ABSENT"] == 1
    assert result["attendance_rate"] == pytest.approx(66.7)
    late = next(s for s in result["students"] if s["student_id"] == "A2")
    assert late["exceptions"][0]["type"] == "LATE_ARRIVAL" and "09:25" in late["exceptions"][0]["reason"]
    absent = next(s for s in result["students"] if s["student_id"] == "A3")
    assert absent["exceptions"][0]["type"] == "ABSENT"


def test_grace_boundary_is_inclusive(school):
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(9, 10), ids["in"])  # exactly start + grace
    db.close()
    assert statuses(period(client, ids["p1"]))["A1"] == "PRESENT"


def test_no_camera_running_means_unverified_not_absent(school):
    client, factory, ids = school
    result = period(client, ids["p1"])
    assert set(statuses(result).values()) == {"UNVERIFIED"}
    assert result["camera_coverage"] is False
    assert result["attendance_rate"] is None  # no decided students -> no rate, not 0 %


def test_camera_of_another_classroom_does_not_count_as_coverage(school):
    client, factory, ids = school
    db = factory()
    other_room = models.Classroom(name="Room 202", is_active=True)
    db.add(other_room)
    db.commit()
    other_cam = add_camera(db, "Other IN", "IN")
    other_cam.classroom_id = other_room.id
    db.commit()
    add_run(db, other_cam.id, at(8, 30), at(12, 0))
    db.close()
    assert set(statuses(period(client, ids["p1"])).values()) == {"UNVERIFIED"}


def test_already_inside_at_start_is_present_and_early_departure_flagged(school):
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(7, 0), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(7, 30), ids["in"])   # long before the window
    add_event(db, ids["A1"], "EXIT", at(9, 30), ids["out"])   # leaves mid-period, no return
    add_event(db, ids["A2"], "ENTRY", at(8, 58), ids["in"])
    add_event(db, ids["A2"], "EXIT", at(9, 20), ids["out"])   # leaves and comes back
    add_event(db, ids["A2"], "ENTRY", at(9, 30), ids["in"])
    db.close()

    result = period(client, ids["p1"])
    rows = {s["student_id"]: s for s in result["students"]}
    assert rows["A1"]["status"] == "PRESENT" and rows["A1"]["inside_at_start"] is True
    assert [e["type"] for e in rows["A1"]["exceptions"]] == ["EARLY_DEPARTURE"]
    assert rows["A1"]["minutes_inside"] == 30.0
    assert [e["type"] for e in rows["A2"]["exceptions"]] == ["LEFT_DURING_PERIOD"]
    assert rows["A2"]["returned_during_period"] is True
    assert rows["A2"]["minutes_inside"] == 50.0


def test_did_not_return_after_break(school):
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(8, 55), ids["in"])
    add_event(db, ids["A1"], "EXIT", at(10, 2), ids["out"])  # break after period 1, never back
    db.close()

    second = period(client, ids["p2"])
    a1 = next(s for s in second["students"] if s["student_id"] == "A1")
    assert a1["status"] == "ABSENT"
    assert [e["type"] for e in a1["exceptions"]] == ["DID_NOT_RETURN_AFTER_BREAK"]


def test_in_progress_and_upcoming_periods_never_report_absent(school):
    _, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    p1 = db.get(models.TimetablePeriod, ids["p1"])
    during = attendance_logic.compute_period(db, p1, DAY, now=datetime.combine(DAY, time(9, 30)))
    assert set(statuses(during).values()) == {"NOT_ARRIVED"} and during["phase"] == "in_progress"
    before = attendance_logic.compute_period(db, p1, DAY, now=datetime.combine(DAY, time(8, 0)))
    assert set(statuses(before).values()) == {"NOT_STARTED"} and before["phase"] == "upcoming"
    db.close()


def test_holiday_suppresses_attendance(school):
    client, factory, ids = school
    r = client.post("/timetable/holidays", json={"date": str(DAY), "name": "Diwali"})
    assert r.status_code == 200, r.text
    result = period(client, ids["p1"])
    assert set(statuses(result).values()) == {"HOLIDAY"} and result["holiday"] == "Diwali"


def test_wrong_weekday_is_rejected(school):
    client, _, ids = school
    r = client.get(f"/timetable/periods/{ids['p1']}/attendance?date={DAY + timedelta(days=1)}")
    assert r.status_code == 400 and "Monday" in r.json()["detail"]


def test_override_replaces_status_keeps_computed_and_is_audited(school):
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    db.close()
    url = f"/timetable/periods/{ids['p1']}/attendance/{ids['A3']}?date={DAY}"
    assert client.put(url, json={"status": "EXCUSED", "reason": ""}).status_code == 422  # reason required
    r = client.put(url, json={"status": "EXCUSED", "reason": "Medical certificate"})
    assert r.status_code == 200, r.text

    a3 = next(s for s in period(client, ids["p1"])["students"] if s["student_id"] == "A3")
    assert a3["status"] == "EXCUSED" and a3["computed_status"] == "ABSENT" and a3["overridden"]
    assert a3["exceptions"] == []
    db = factory()
    entry = db.query(models.AuditLog).filter_by(action="attendance.override").one()
    assert entry.username == "admin" and "Medical certificate" in entry.details
    db.close()

    assert client.delete(url).status_code == 200
    a3 = next(s for s in period(client, ids["p1"])["students"] if s["student_id"] == "A3")
    assert a3["status"] == "ABSENT" and not a3["overridden"]


def test_override_rejected_for_student_outside_roster(school):
    client, factory, ids = school
    db = factory()
    outsider = add_student(db, "Z9").id
    db.close()
    r = client.put(f"/timetable/periods/{ids['p1']}/attendance/{outsider}?date={DAY}",
                   json={"status": "PRESENT", "reason": "test reason"})
    assert r.status_code == 400


def test_overlapping_periods_rejected(school):
    client, _, ids = school
    base = {"classroom_id": ids["room"], "course_name": "Chemistry", "day_of_week": MONDAY}
    r = client.post("/timetable/periods", json={**base, "class_name": "CSE-A", "start_time": "09:30", "end_time": "10:30"})
    assert r.status_code == 409 and "CSE-A" in r.json()["detail"]
    r = client.post("/timetable/periods", json={**base, "class_name": "CSE-B", "start_time": "09:30", "end_time": "10:30"})
    assert r.status_code == 409 and "classroom" in r.json()["detail"]
    r = client.post("/timetable/periods", json={**base, "class_name": "CSE-B", "start_time": "11:30", "end_time": "11:00"})
    assert r.status_code == 422
    r = client.post("/timetable/periods", json={**base, "class_name": "CSE-B", "start_time": "11:30", "end_time": "12:30"})
    assert r.status_code == 200 and r.json()["day_name"] == "Monday"


def test_daily_and_monthly_summaries_use_the_same_statuses(school):
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(8, 55), ids["in"])
    add_event(db, ids["A2"], "ENTRY", at(9, 20), ids["in"])
    db.close()

    daily = client.get(f"/timetable/daily?date={DAY}").json()
    assert [p["course_name"] for p in daily["periods"]] == ["Maths", "Physics"]
    assert daily["periods"][0]["counts"]["PRESENT"] == 1 and daily["periods"][0]["counts"]["LATE"] == 1
    assert {e["type"] for e in daily["exceptions"]} >= {"LATE_ARRIVAL", "ABSENT"}

    monthly = client.get(f"/timetable/monthly?year={DAY.year}&month={DAY.month}").json()
    rows = {s["student_id"]: s for s in monthly["students"]}
    assert rows["A1"]["present"] == 2 and rows["A1"]["attendance_rate"] == 100.0
    assert rows["A2"]["late"] == 1 and rows["A2"]["present"] == 1
    assert rows["A3"]["absent"] == 2 and rows["A3"]["attendance_rate"] == 0.0
    assert "T1" not in rows  # staff are not in class attendance


# --- presence sessions, manual events, inclusive ranges ---------------------------------

def test_sessions_pair_entries_and_exits_and_flag_unpaired(school):
    client, factory, ids = school
    db = factory()
    add_event(db, ids["A1"], "ENTRY", at(9, 0), ids["in"])
    add_event(db, ids["A1"], "EXIT", at(10, 30), ids["out"])
    add_event(db, ids["A2"], "EXIT", at(11, 0), ids["out"])   # exit without entry
    add_event(db, ids["A3"], "ENTRY", at(9, 5), ids["in"])    # never exits (past day)
    db.close()

    sessions = client.get(f"/attendance/sessions?start_date={DAY}&end_date={DAY}").json()
    by_id = {s["student_id"]: s for s in sessions}
    assert by_id["A1"]["status"] == "complete" and by_id["A1"]["duration_minutes"] == 90.0
    assert by_id["A1"]["entry_camera"] == "Door IN" and by_id["A1"]["exit_camera"] == "Door OUT"
    assert by_id["A2"]["status"] == "no_entry" and by_id["A3"]["status"] == "no_exit"
    filtered = client.get(f"/attendance/sessions?start_date={DAY}&end_date={DAY}&q=a1").json()
    assert [s["student_id"] for s in filtered] == ["A1"]


def test_event_date_filter_is_inclusive_of_the_end_day(school):
    client, factory, ids = school
    db = factory()
    late_evening = add_event(db, ids["A1"], "ENTRY", at(23, 59), ids["in"]).id
    next_day = add_event(db, ids["A1"], "EXIT", utc_naive(DAY + timedelta(days=1), time(0, 0)), ids["out"]).id
    db.close()
    ids_returned = [e["id"] for e in client.get(f"/attendance/?start_date={DAY}&end_date={DAY}").json()]
    assert late_evening in ids_returned and next_day not in ids_returned
    assert client.get(f"/attendance/?start_date={DAY}&end_date={DAY - timedelta(days=1)}").status_code == 400


def test_manual_event_records_operator_reason_and_audit(school):
    client, factory, ids = school
    r = client.post("/attendance/manual", json={"student_id": ids["A3"], "event_type": "ENTRY",
                                                "timestamp": f"{DAY}T09:02:00", "reason": "Camera was blocked"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "manual" and body["note"] == "Camera was blocked" and body["confidence"] == 100
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    assert client.post("/attendance/manual", json={"student_id": ids["A3"], "event_type": "ENTRY",
                                                   "timestamp": future, "reason": "nope"}).status_code == 400

    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    assert db.query(models.AuditLog).filter_by(action="attendance.manual_event").count() == 1
    db.close()
    assert statuses(period(client, ids["p1"]))["A3"] == "PRESENT"  # naive time = server-local 09:02


# --- exports -------------------------------------------------------------------------------

def test_csv_exports_match_api_and_neutralise_formulas(school):
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    evil = add_student(db, "=HYPERLINK(1)", name="+cmd")
    evil.class_name = "CSE-A"
    db.commit()
    add_event(db, ids["A1"], "ENTRY", at(8, 55), ids["in"])
    add_event(db, evil.id, "ENTRY", at(8, 56), ids["in"])
    db.close()

    r = client.get(f"/reports/period.csv?period_id={ids['p1']}&date={DAY}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8-sig")
    assert "A1" in text and "PRESENT" in text and "ABSENT" in text
    assert "'=HYPERLINK(1)" in text and "'+cmd" in text and "\n=HYPERLINK" not in text

    sessions = client.get(f"/reports/sessions.csv?start_date={DAY}&end_date={DAY}").content.decode("utf-8-sig")
    assert sessions.count("\r\n") == 1 + 2  # header + two open sessions
    monthly = client.get(f"/reports/monthly.csv?year={DAY.year}&month={DAY.month}").content.decode("utf-8-sig")
    assert monthly.splitlines()[0].startswith("ID,Name,Class,Periods")
    daily = client.get(f"/reports/daily.csv?date={DAY}").content.decode("utf-8-sig")
    assert "Maths" in daily and "Physics" in daily


@pytest.mark.parametrize("url", ["/reports/monthly.pdf?year={y}&month={m}", "/reports/daily.pdf?date={d}",
                                 "/reports/sessions.pdf?start_date={d}&end_date={d}"])
def test_pdf_exports_are_real_pdfs(school, url):
    client, factory, ids = school
    db = factory()
    add_event(db, ids["A1"], "ENTRY", at(8, 55), ids["in"])
    db.close()
    r = client.get(url.format(y=DAY.year, m=DAY.month, d=DAY))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf" and r.content[:5] == b"%PDF-"


def test_leaving_during_a_running_period_is_not_yet_an_early_departure(school):
    _, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(8, 55), ids["in"])
    add_event(db, ids["A1"], "EXIT", at(9, 20), ids["out"])
    p1 = db.get(models.TimetablePeriod, ids["p1"])
    during = attendance_logic.compute_period(db, p1, DAY, now=datetime.combine(DAY, time(9, 30)))
    after = attendance_logic.compute_period(db, p1, DAY, now=datetime.combine(DAY, time(10, 30)))
    db.close()
    a1_during = next(s for s in during["students"] if s["student_id"] == "A1")
    a1_after = next(s for s in after["students"] if s["student_id"] == "A1")
    assert [e["type"] for e in a1_during["exceptions"]] == ["OUT_OF_CLASS"]
    assert [e["type"] for e in a1_after["exceptions"]] == ["EARLY_DEPARTURE"]


def test_upcoming_period_never_marks_people_inside_as_present(school):
    _, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 0), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(8, 30), ids["in"])  # inside before an upcoming period
    p2 = db.get(models.TimetablePeriod, ids["p2"])
    result = attendance_logic.compute_period(db, p2, DAY, now=datetime.combine(DAY, time(9, 0)))
    db.close()
    assert set(statuses(result).values()) == {"NOT_STARTED"}
    assert result["attendance_rate"] is None and result["decided_count"] == 0


def test_period_does_not_apply_before_it_was_added_to_the_timetable(school):
    """A weekly period created later must not produce (possibly absent) occurrences on earlier dates"""
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 0), at(12, 0))  # a camera was running that day
    late_period = models.TimetablePeriod(classroom_id=ids["room"], class_name="CSE-A", course_name="Chemistry",
                                         day_of_week=MONDAY, start_time=time(14, 0), end_time=time(15, 0), is_active=True)
    late_period.created_at = at(8, 0, DAY + timedelta(days=1))  # added the day after DAY
    db.add(late_period)
    db.commit()
    db.close()

    daily = client.get(f"/timetable/daily?date={DAY}").json()
    assert [p["course_name"] for p in daily["periods"]] == ["Maths", "Physics"]  # no Chemistry, no "absent"
    next_monday = DAY + timedelta(days=7)
    assert "Chemistry" in [p["course_name"] for p in client.get(f"/timetable/daily?date={next_monday}").json()["periods"]]

    # On DAY, A3 (no entry, camera running) is absent from exactly the two periods that existed
    absences = [e["course_name"] for e in daily["exceptions"] if e["student_id"] == "A3" and e["type"] == "ABSENT"]
    assert sorted(absences) == ["Maths", "Physics"]
    # The monthly summary counts Chemistry only on Mondays from its creation date onwards
    db = factory()
    month = attendance_logic.monthly_summary(db, DAY.year, DAY.month, now=datetime.combine(DAY, time(23, 0)))
    db.close()
    a3 = next(s for s in month["students"] if s["student_id"] == "A3")
    assert a3["scheduled"] == sum(len(attendance_logic.periods_on(factory(), d))
                                  for d in [DAY - timedelta(days=7 * k) for k in range(5)]
                                  if d.month == DAY.month and d.year == DAY.year)
    assert a3["scheduled"] % 2 == 0  # only Maths + Physics days, never Chemistry before it existed


def test_period_added_today_counts_today(school):
    _, factory, ids = school
    db = factory()
    today = date.today()
    period = models.TimetablePeriod(classroom_id=ids["room"], class_name="CSE-A", course_name="Today Course",
                                    day_of_week=today.weekday(), start_time=time(0, 0), end_time=time(23, 59), is_active=True)
    db.add(period)
    db.commit()  # created_at = now
    names = [p.course_name for p in attendance_logic.periods_on(db, today)]
    earlier = [p.course_name for p in attendance_logic.periods_on(db, today - timedelta(days=7))]
    db.close()
    assert "Today Course" in names and "Today Course" not in earlier


def test_period_attendance_before_the_period_existed_is_refused(school):
    client, factory, ids = school
    db = factory()
    late = models.TimetablePeriod(classroom_id=ids["room"], class_name="CSE-A", course_name="Biology",
                                  day_of_week=MONDAY, start_time=time(15, 0), end_time=time(16, 0), is_active=True)
    late.created_at = at(8, 0, DAY + timedelta(days=1))
    db.add(late)
    db.commit()
    late_id = late.id
    db.close()
    r = client.get(f"/timetable/periods/{late_id}/attendance?date={DAY}")
    assert r.status_code == 400 and "added to the timetable" in r.json()["detail"]
    r = client.put(f"/timetable/periods/{late_id}/attendance/{ids['A1']}?date={DAY}", json={"status": "PRESENT", "reason": "backfill"})
    assert r.status_code == 400  # no overrides for dates before the period existed
    assert client.get(f"/timetable/periods/{late_id}/attendance?date={DAY + timedelta(days=7)}").status_code == 200
