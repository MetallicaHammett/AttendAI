"""
Professor (staff) portal: access control, weekly timetable, class registers built from
camera evidence, and saving/reopening registers. Synthetic data in an isolated SQLite file.
"""
from datetime import date, datetime, time, timedelta

import pytest

from app import models
from app.security import create_access_token
from helpers import ASGIClient, add_camera, add_event, add_student, utc_naive

# A Monday well in the past: every period on it is over
DAY = date.today() - timedelta(days=date.today().weekday() + 14)


def at(hh, mm, day=DAY):
    return utc_naive(day, time(hh, mm))


def add_run(db, camera_id, start, end, heartbeat=None):
    db.add(models.CameraRun(camera_id=camera_id, started_at=start, stopped_at=end, last_heartbeat_at=heartbeat,
                            feed_kind="live"))
    db.commit()


def client_for(username):
    from app.main import app
    return ASGIClient(app, headers={"Authorization": f"Bearer {create_access_token({'sub': username})}"})


@pytest.fixture
def campus(api):
    """Room 101 (IN/OUT cameras) and a main gate camera with no classroom. Class CSE-A
    (A1-A3, an inactive A4, a staff member) and CSE-B (B1). Two professors:
    prof_rao teaches CSE-A Maths (Mon 09:00-10:00) and CSE-A Physics (Mon 10:15-11:15);
    prof_iyer teaches CSE-B Chemistry (Mon 09:00-10:00, Room 102)."""
    admin, factory = api
    db = factory()
    room, room2 = models.Classroom(name="Room 101"), models.Classroom(name="Room 102")
    db.add_all([room, room2])
    db.commit()
    cam_in, cam_out = add_camera(db, "Room 101 IN", "IN"), add_camera(db, "Room 101 OUT", "OUT")
    cam_in.classroom_id = cam_out.classroom_id = room.id
    gate = add_camera(db, "Main gate", "IN")
    ids = {"room": room.id, "in": cam_in.id, "gate": gate.id}
    for sid, cls in (("A1", "CSE-A"), ("A2", "CSE-A"), ("A3", "CSE-A"), ("A4", "CSE-A"), ("B1", "CSE-B")):
        s = add_student(db, sid)
        s.class_name = cls
        ids[sid] = s.id
    db.query(models.Student).filter_by(student_id="A4").update({"is_active": False})
    staff = add_student(db, "T1", name="Teacher")
    staff.class_name, staff.person_type = "CSE-A", "staff"
    ids["T1"] = staff.id
    for name, role in (("prof_rao", "professor"), ("prof_iyer", "professor"), ("viewer1", "viewer"),
                       ("operator1", "operator")):
        user = models.User(email=f"{name}@example.com", username=name, hashed_password="unused", role=role)
        db.add(user)
        db.commit()
        ids[name] = user.id
    student_user = models.User(email="a1@example.com", username="a1user", hashed_password="unused", role="student",
                               student_db_id=ids["A1"])
    db.add(student_user)
    periods = {
        "maths": models.TimetablePeriod(classroom_id=room.id, class_name="CSE-A", course_code="MA101", course_name="Maths",
                                        day_of_week=0, start_time=time(9, 0), end_time=time(10, 0), grace_minutes=10,
                                        professor_user_id=ids["prof_rao"]),
        "physics": models.TimetablePeriod(classroom_id=room.id, class_name="CSE-A", course_name="Physics", day_of_week=0,
                                          start_time=time(10, 15), end_time=time(11, 15), grace_minutes=10,
                                          professor_user_id=ids["prof_rao"]),
        "chem": models.TimetablePeriod(classroom_id=room2.id, class_name="CSE-B", course_name="Chemistry", day_of_week=0,
                                       start_time=time(9, 0), end_time=time(10, 0), professor_user_id=ids["prof_iyer"]),
    }
    for p in periods.values():
        p.created_at = at(8, 0, DAY - timedelta(days=30))
    db.add_all(periods.values())
    db.commit()
    ids.update({k: p.id for k, p in periods.items()})
    db.close()
    return admin, factory, ids


def register(client, period_id, day=DAY):
    r = client.get(f"/staff/periods/{period_id}/register?date={day}")
    assert r.status_code == 200, r.text
    return r.json()


def by_sid(reg):
    return {s["student_id"]: s for s in reg["students"]}


def decisions(reg, overrides=None, reasons=None):
    """Every student's current status (anything undecided -> PRESENT) unless overridden"""
    overrides, reasons = overrides or {}, reasons or {}
    out = []
    for s in reg["students"]:
        current = s["status"] if s["status"] in ("PRESENT", "LATE", "ABSENT", "EXCUSED") else "PRESENT"
        status = overrides.get(s["student_id"], current)
        out.append({"student_db_id": s["student_db_id"], "status": status, "reason": reasons.get(s["student_id"]),
                    "base_status": s["final"]["status"] if s["final"] else None})
    return out


def save(client, reg, period_id, overrides=None, reasons=None, version=None, day=DAY):
    body = {"expected_version": reg["register"]["version"] if version is None else version,
            "decisions": decisions(reg, overrides, reasons)}
    return client.put(f"/staff/periods/{period_id}/register?date={day}", json=body)


# --- access control -------------------------------------------------------------------

def test_professor_sees_only_their_own_timetable_and_registers(campus):
    admin, _, ids = campus
    rao, iyer = client_for("prof_rao"), client_for("prof_iyer")
    week = rao.get(f"/staff/timetable?week_of={DAY + timedelta(days=3)}").json()
    assert week["week_start"] == DAY.isoformat() and week["assigned_periods"] == 2
    monday = week["days"][0]
    assert [p["course_name"] for p in monday["periods"]] == ["Maths", "Physics"]
    assert monday["periods"][0]["classroom"] == "Room 101" and monday["periods"][0]["course_code"] == "MA101"
    assert all(not d["periods"] for d in week["days"][1:])
    assert [p["course_name"] for d in iyer.get(f"/staff/timetable?week_of={DAY}").json()["days"] for p in d["periods"]] \
        == ["Chemistry"]
    # Another professor's class: refused, not just hidden
    assert rao.get(f"/staff/periods/{ids['chem']}/register?date={DAY}").status_code == 403
    assert rao.put(f"/staff/periods/{ids['chem']}/register?date={DAY}",
                   json={"expected_version": 0, "decisions": []}).status_code == 403
    # Admins oversee every period, or one professor's
    assert admin.get(f"/staff/timetable?week_of={DAY}").json()["assigned_periods"] == 3
    assert admin.get(f"/staff/timetable?week_of={DAY}&professor_id={ids['prof_iyer']}").json()["assigned_periods"] == 1
    assert admin.get(f"/staff/periods/{ids['chem']}/register?date={DAY}").status_code == 200


@pytest.mark.parametrize("username", ["viewer1", "operator1", "a1user"])
def test_other_roles_cannot_use_the_staff_portal(campus, username):
    _, _, ids = campus
    client = client_for(username)
    assert client.get("/staff/timetable").status_code == 403
    assert client.get(f"/staff/periods/{ids['maths']}/register?date={DAY}").status_code == 403
    assert client.put(f"/staff/periods/{ids['maths']}/register?date={DAY}",
                      json={"expected_version": 0, "decisions": []}).status_code == 403
    assert client.get("/staff/history").status_code == 403


@pytest.mark.parametrize("method,url", [
    ("get", "/students/"), ("get", "/cameras/"), ("get", "/users/"), ("get", "/users/audit-log"),
    ("get", "/dashboard/stats"), ("get", "/timetable/periods"), ("get", "/video/live"),
    ("post", "/video/1/start"), ("get", "/reports/daily.csv?date=2026-10-19"), ("get", "/portal/me/attendance"),
])
def test_professor_has_no_admin_operator_or_viewer_access(campus, method, url):
    r = getattr(client_for("prof_rao"), method)(url)
    assert r.status_code == 403, f"professor reached {method.upper()} {url}: {r.status_code}"


def test_professor_cannot_change_attendance_through_the_operator_endpoint(campus):
    _, _, ids = campus
    r = client_for("prof_rao").put(f"/timetable/periods/{ids['maths']}/attendance/{ids['A1']}?date={DAY}",
                                   json={"status": "PRESENT", "reason": "trying the operator route"})
    assert r.status_code == 403


def test_unknown_period_and_wrong_weekday(campus):
    admin, _, ids = campus
    assert admin.get(f"/staff/periods/9999/register?date={DAY}").status_code == 404
    assert admin.get(f"/staff/periods/{ids['maths']}/register?date={DAY + timedelta(days=1)}").status_code == 400


# --- register contents ----------------------------------------------------------------

def test_roster_is_the_class_only_active_students_no_staff(campus):
    reg = register(client_for("prof_rao"), campus[2]["maths"])
    assert sorted(by_sid(reg)) == ["A1", "A2", "A3"]
    assert reg["roster_size"] == 3 and reg["period"]["professor"] == "prof_rao"


def test_classroom_camera_evidence_building_only_and_not_detected(campus):
    _, factory, ids = campus
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_run(db, ids["gate"], at(8, 0), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(8, 58), ids["in"])     # seen by the classroom camera
    add_event(db, ids["A2"], "ENTRY", at(8, 50), ids["gate"])   # only at the main gate
    add_event(db, ids["T1"], "ENTRY", at(8, 55), ids["in"])     # staff: never on the roster
    db.close()

    reg = register(client_for("prof_rao"), ids["maths"])
    rows = by_sid(reg)
    assert rows["A1"]["ai_status"] == "PRESENT" and rows["A1"]["suggested_status"] == "PRESENT"
    assert rows["A1"]["evidence"]["kind"] == "classroom_camera" and rows["A1"]["evidence"]["camera"] == "Room 101 IN"
    # Entering the building is not attending the lecture
    assert rows["A2"]["ai_status"] == "SEEN_ELSEWHERE" and rows["A2"]["suggested_status"] == "UNVERIFIED"
    # Not seen by the classroom camera (which was running) + a hint that the gate saw them
    assert [f["code"] for f in rows["A2"]["review_flags"]] == ["BUILDING_ONLY", "NOT_DETECTED"]
    assert rows["A2"]["evidence"]["kind"] == "other_camera" and rows["A2"]["evidence"]["camera"] == "Main gate"
    # Not recognised while the classroom camera ran: never suggested absent
    assert rows["A3"]["ai_status"] == "NOT_DETECTED" and rows["A3"]["status"] == "UNVERIFIED"
    assert "NOT_DETECTED" in [f["code"] for f in rows["A3"]["review_flags"]]
    assert reg["counts"] == {"PRESENT": 1, "LATE": 0, "ABSENT": 0, "EXCUSED": 0, "UNVERIFIED": 2}
    assert reg["coverage"]["state"] == "full" and reg["coverage"]["classroom_cameras"] == ["Room 101 IN"]
    assert reg["can_save"] is True and reg["register"]["submitted"] is False


def test_late_arrival_and_events_are_matched_to_the_right_period(campus):
    _, factory, ids = campus
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(9, 25), ids["in"])               # late for Maths (grace to 09:10)
    add_event(db, ids["A2"], "ENTRY", at(10, 20), ids["in"])              # arrives for Physics only
    db.close()
    rao = client_for("prof_rao")
    maths, physics = by_sid(register(rao, ids["maths"])), by_sid(register(rao, ids["physics"]))
    assert maths["A1"]["suggested_status"] == "LATE"
    assert "LATE_ARRIVAL" in [f["code"] for f in maths["A1"]["review_flags"]]
    assert maths["A2"]["suggested_status"] == "UNVERIFIED"
    assert physics["A2"]["suggested_status"] == "PRESENT" and physics["A2"]["evidence"]["at"].endswith("10:20:00")


def test_no_camera_running_means_unverified_for_everyone(campus):
    reg = register(client_for("prof_rao"), campus[2]["maths"])
    assert reg["coverage"]["state"] == "none" and reg["coverage"]["covered_percent"] == 0
    assert {s["status"] for s in reg["students"]} == {"UNVERIFIED"}
    assert all("NO_CAMERA_COVERAGE" in [f["code"] for f in s["review_flags"]] for s in reg["students"])


def test_classroom_without_its_own_camera_never_relies_on_other_cameras(campus):
    """Room 102 has no camera: a gate-camera entry is building-only evidence and a student
    not seen at the gate is not 'absent' either"""
    _, factory, ids = campus
    db = factory()
    add_run(db, ids["gate"], at(8, 0), at(12, 0))
    add_event(db, ids["B1"], "ENTRY", at(8, 50), ids["gate"])
    db.close()
    reg = register(client_for("prof_iyer"), ids["chem"])
    assert reg["coverage"]["state"] == "no_classroom_camera"
    assert by_sid(reg)["B1"]["suggested_status"] == "UNVERIFIED"
    assert by_sid(reg)["B1"]["ai_status"] == "SEEN_ELSEWHERE"


def test_camera_that_stopped_reporting_is_flagged_as_an_outage(campus):
    _, factory, ids = campus
    db = factory()
    # Never stopped, last heartbeat at 09:20: coverage ends there
    add_run(db, ids["in"], at(8, 30), None, heartbeat=at(9, 20))
    db.close()
    reg = register(client_for("prof_rao"), ids["maths"])
    assert reg["coverage"]["outage"] is True and reg["coverage"]["state"] == "partial"
    assert 0 < reg["coverage"]["covered_percent"] < 100
    assert all("CAMERA_OUTAGE" in [f["code"] for f in s["review_flags"]] for s in reg["students"])


def test_holiday_and_upcoming_periods_cannot_be_saved(campus):
    admin, factory, ids = campus
    db = factory()
    db.add(models.Holiday(date=DAY, name="Founders Day"))
    db.commit()
    db.close()
    rao = client_for("prof_rao")
    reg = register(rao, ids["maths"])
    assert reg["can_save"] is False and "Founders Day" in reg["save_blocked_reason"]
    assert save(rao, reg, ids["maths"]).status_code == 400
    week = rao.get(f"/staff/timetable?week_of={DAY}").json()
    assert week["days"][0]["holiday"] == "Founders Day" and week["days"][0]["periods"][0]["state"] == "holiday"

    future = DAY + timedelta(weeks=8)
    upcoming = register(rao, ids["maths"], future)
    assert upcoming["phase"] == "upcoming" and upcoming["can_save"] is False
    assert {s["status"] for s in upcoming["students"]} == {"NOT_STARTED"}
    assert rao.put(f"/staff/periods/{ids['maths']}/register?date={future}",
                   json={"expected_version": 0, "decisions": decisions(upcoming)}).status_code == 400


def test_dates_before_the_period_existed_are_not_scheduled(campus):
    rao = client_for("prof_rao")
    early = DAY - timedelta(weeks=8)
    assert rao.get(f"/staff/periods/{campus[2]['maths']}/register?date={early}").status_code == 400
    week = rao.get(f"/staff/timetable?week_of={early}").json()
    assert week["days"][0]["periods"][0]["state"] == "not_scheduled"


# --- saving ---------------------------------------------------------------------------

@pytest.fixture
def morning(campus):
    """Classroom camera ran; A1 entered on time, A2 only at the gate, A3 not seen"""
    admin, factory, ids = campus
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(8, 58), ids["in"])
    add_event(db, ids["A2"], "ENTRY", at(8, 50), ids["gate"])
    db.close()
    return admin, factory, ids


def test_every_student_must_be_decided_before_saving(morning):
    _, factory, ids = morning
    rao = client_for("prof_rao")
    reg = register(rao, ids["maths"])
    body = {"expected_version": 0, "decisions": decisions(reg)[:2]}
    r = rao.put(f"/staff/periods/{ids['maths']}/register?date={DAY}", json=body)
    assert r.status_code == 400 and "Decide every student" in r.json()["detail"]
    other_class = {"student_db_id": ids["B1"], "status": "PRESENT", "base_status": None}
    r = rao.put(f"/staff/periods/{ids['maths']}/register?date={DAY}",
                json={"expected_version": 0, "decisions": decisions(reg) + [other_class]})
    assert r.status_code == 400 and "not on this class" in r.json()["detail"]
    db = factory()
    assert db.query(models.AttendanceOverride).count() == 0 and db.query(models.AttendanceRegister).count() == 0
    db.close()


@pytest.mark.parametrize("overrides", [{"A1": "ABSENT"}, {"A3": "EXCUSED"}])
def test_reason_required_against_camera_evidence_and_for_excusals(morning, overrides):
    _, factory, ids = morning
    rao = client_for("prof_rao")
    r = save(rao, register(rao, ids["maths"]), ids["maths"], overrides=overrides)
    assert r.status_code == 400 and "reason" in r.json()["detail"]
    db = factory()
    assert db.query(models.AttendanceOverride).count() == 0
    db.close()


def test_save_records_decisions_audit_and_feeds_existing_reports(morning):
    admin, factory, ids = morning
    rao = client_for("prof_rao")
    reg = register(rao, ids["maths"])
    # A2 was at the gate: the professor saw them in class. A3 confirmed absent.
    r = save(rao, reg, ids["maths"], overrides={"A2": "PRESENT", "A3": "ABSENT"})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["register"]["submitted"] and saved["register"]["version"] == 1
    assert saved["register"]["submitted_by"] == "prof_rao" and saved["changed_count"] == 3
    rows = by_sid(saved)
    assert {k: v["status"] for k, v in rows.items()} == {"A1": "PRESENT", "A2": "PRESENT", "A3": "ABSENT"}
    # The camera's view stays visible next to the professor's decision
    assert rows["A2"]["ai_status"] == "SEEN_ELSEWHERE" and rows["A2"]["final"]["by"] == "prof_rao"
    assert rows["A3"]["ai_status"] == "NOT_DETECTED"
    assert saved["counts"]["UNVERIFIED"] == 0 and saved["confirmed_count"] == 3

    entry = admin.get("/users/audit-log").json()[0]
    assert entry["action"] == "attendance.register_submit" and entry["username"] == "prof_rao"
    assert entry["details"]["counts"] == {"PRESENT": 2, "LATE": 0, "ABSENT": 1, "EXCUSED": 0}
    change = next(c for c in entry["details"]["changes"] if c["student_id"] == "A2")
    assert change["ai_status"] == "SEEN_ELSEWHERE" and change["before"] is None and change["after"] == "PRESENT"

    # Existing staff reports and the student's own page use the professor's decisions
    period_view = admin.get(f"/timetable/periods/{ids['maths']}/attendance?date={DAY}").json()
    assert {s["student_id"]: s["status"] for s in period_view["students"]} == \
        {"A1": "PRESENT", "A2": "PRESENT", "A3": "ABSENT"}
    week = rao.get(f"/staff/timetable?week_of={DAY}").json()["days"][0]["periods"]
    assert [p["state"] for p in week] == ["submitted", "awaiting_review"]
    history = rao.get("/staff/history").json()
    assert len(history) == 1 and history[0]["counts"]["PRESENT"] == 2 and history[0]["submitted_by"] == "prof_rao"
    assert client_for("prof_iyer").get("/staff/history").json() == []


def test_duplicate_or_stale_submission_is_refused(morning):
    _, factory, ids = morning
    rao = client_for("prof_rao")
    reg = register(rao, ids["maths"])
    assert save(rao, reg, ids["maths"], overrides={"A3": "ABSENT"}).status_code == 200
    # The same (now stale) copy submitted again, e.g. a double click or a second tab
    r = save(rao, reg, ids["maths"], overrides={"A3": "ABSENT"})
    assert r.status_code == 409 and "Reload" in r.json()["detail"]
    db = factory()
    assert db.query(models.AttendanceRegister).one().version == 1
    db.close()


def test_reopening_a_submitted_register_needs_a_reason_to_change_a_decision(morning):
    admin, factory, ids = morning
    rao = client_for("prof_rao")
    assert save(rao, register(rao, ids["maths"]), ids["maths"], overrides={"A3": "ABSENT"}).status_code == 200
    reopened = register(rao, ids["maths"])
    assert reopened["register"]["version"] == 1 and by_sid(reopened)["A3"]["final"]["status"] == "ABSENT"

    r = save(rao, reopened, ids["maths"], overrides={"A3": "PRESENT"})
    assert r.status_code == 400 and "changing a saved decision" in r.json()["detail"]
    r = save(rao, reopened, ids["maths"], overrides={"A3": "PRESENT"}, reasons={"A3": "Was in the back row"})
    assert r.status_code == 200
    assert r.json()["register"]["version"] == 2 and by_sid(r.json())["A3"]["final"]["reason"] == "Was in the back row"
    entry = admin.get("/users/audit-log").json()[0]
    assert entry["action"] == "attendance.register_update" and entry["details"]["version"] == 2
    assert entry["details"]["changes"] == [{"student_db_id": ids["A3"], "student_id": "A3", "ai_status": "NOT_DETECTED",
                                           "before": "ABSENT", "after": "PRESENT", "reason": "Was in the back row"}]


def test_unchanged_saved_excusal_does_not_block_a_later_save(morning):
    """Regression: re-saving a register that already contains an excusal (or an absence
    against camera evidence) must not ask for those reasons again"""
    _, factory, ids = morning
    rao = client_for("prof_rao")
    r = save(rao, register(rao, ids["maths"]), ids["maths"], overrides={"A1": "ABSENT", "A3": "EXCUSED"},
             reasons={"A1": "Left before roll call", "A3": "Medical certificate"})
    assert r.status_code == 200, r.text
    reopened = register(rao, ids["maths"])
    r = save(rao, reopened, ids["maths"], overrides={"A2": "LATE"}, reasons={"A2": "Arrived after attendance"})
    assert r.status_code == 200, r.text
    rows = by_sid(r.json())
    assert rows["A3"]["final"]["reason"] == "Medical certificate"  # kept, not overwritten
    assert rows["A1"]["final"]["reason"] == "Left before roll call"
    assert r.json()["changed_count"] == 1


def test_default_reasons_name_the_actual_evidence(morning):
    _, factory, ids = morning
    db = factory()
    add_event(db, ids["A3"], "ENTRY", at(8, 57))  # no camera: recorded manually by an operator
    db.query(models.AttendanceEvent).filter_by(student_id=ids["A3"]).update({"source": "manual"})
    db.commit()
    db.close()
    rao = client_for("prof_rao")
    rows = by_sid(save(rao, register(rao, ids["maths"]), ids["maths"]).json())
    assert rows["A1"]["final"]["reason"] == "Confirmed by professor; classroom camera evidence agrees"
    assert rows["A3"]["final"]["reason"] == "Confirmed by professor; manual entry event agrees"
    assert rows["A2"]["final"]["reason"] == "Confirmed by professor in the class register"  # gate only: no evidence


def test_change_made_by_someone_else_after_loading_is_not_overwritten(morning):
    admin, factory, ids = morning
    rao = client_for("prof_rao")
    reg = register(rao, ids["maths"])
    # An operator records an excusal through the existing override endpoint meanwhile
    r = client_for("operator1").put(f"/timetable/periods/{ids['maths']}/attendance/{ids['A3']}?date={DAY}",
                                    json={"status": "EXCUSED", "reason": "Medical certificate"})
    assert r.status_code == 200
    r = save(rao, reg, ids["maths"], overrides={"A3": "ABSENT"})
    assert r.status_code == 409 and "Student A3" in r.json()["detail"]
    db = factory()
    assert db.query(models.AttendanceOverride).filter_by(student_id=ids["A3"]).one().status == "EXCUSED"
    assert db.query(models.AttendanceRegister).count() == 0
    db.close()


def test_admin_can_submit_any_register(morning):
    admin, _, ids = morning
    reg = register(admin, ids["chem"])
    r = save(admin, reg, ids["chem"])
    assert r.status_code == 200 and r.json()["register"]["submitted_by"] == "admin"


# --- administrator assignment ---------------------------------------------------------

PERIOD = {"class_name": "CSE-C", "course_name": "Biology", "day_of_week": 2, "start_time": "09:00",
          "end_time": "10:00"}


def test_admin_assigns_only_active_professors(campus):
    admin, factory, ids = campus
    body = {**PERIOD, "classroom_id": ids["room"]}
    for bad in (ids["viewer1"], 99999):
        r = admin.post("/timetable/periods", json={**body, "professor_user_id": bad})
        assert r.status_code == 400 and "professor" in r.json()["detail"]
    r = admin.post("/timetable/periods", json={**body, "professor_user_id": ids["prof_iyer"]})
    assert r.status_code == 200 and r.json()["professor_username"] == "prof_iyer"
    # Reassign to another professor, then unassign
    period_id = r.json()["id"]
    r = admin.put(f"/timetable/periods/{period_id}", json={**body, "professor_user_id": ids["prof_rao"]})
    assert r.status_code == 200 and r.json()["professor_user_id"] == ids["prof_rao"]
    assert admin.get("/users/audit-log").json()[0]["details"]["professor_user_id"] == ids["prof_rao"]
    r = admin.put(f"/timetable/periods/{period_id}", json={**body, "professor_user_id": None})
    assert r.status_code == 200 and r.json()["professor_username"] is None


def test_a_professor_cannot_be_booked_for_two_classes_at_once(campus):
    admin, _, ids = campus
    db_room = admin.post("/timetable/classrooms", json={"name": "Lab 1"}).json()["id"]
    r = admin.post("/timetable/periods", json={**PERIOD, "day_of_week": 0, "classroom_id": db_room,
                                              "start_time": "09:30", "end_time": "10:30",
                                              "professor_user_id": ids["prof_rao"]})
    assert r.status_code == 409 and "professor already teaches" in r.json()["detail"]


def test_operator_cannot_assign_professors(campus):
    _, _, ids = campus
    r = client_for("operator1").post("/timetable/periods", json={**PERIOD, "classroom_id": ids["room"],
                                                                 "professor_user_id": ids["prof_rao"]})
    assert r.status_code == 403


def test_admin_creates_a_professor_account_and_can_change_its_role(campus):
    admin, _, _ = campus
    r = admin.post("/users/", json={"username": "prof_new", "email": "prof_new@college.edu",
                                    "password": "initial-pass-1", "role": "professor"})
    assert r.status_code == 201 and r.json()["role"] == "professor"
    assert admin.patch(f"/users/{r.json()['id']}", json={"role": "viewer"}).json()["role"] == "viewer"
    assert admin.patch(f"/users/{r.json()['id']}", json={"role": "professor"}).json()["role"] == "professor"
    # A professor account is never linked to a student record
    r = admin.post("/users/", json={"username": "prof_x", "email": "px@college.edu", "password": "initial-pass-1",
                                    "role": "professor", "student_db_id": 1})
    assert r.status_code == 422


def test_deactivated_professor_loses_access(campus):
    _, factory, ids = campus
    db = factory()
    db.query(models.User).filter_by(username="prof_rao").update({"is_active": False})
    db.commit()
    db.close()
    assert client_for("prof_rao").get("/staff/timetable").status_code == 401
