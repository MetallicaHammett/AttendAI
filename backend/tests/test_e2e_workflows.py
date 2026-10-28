"""
End-to-end workflows through the real API on an isolated SQLite file: real sign-ins
(bcrypt-hashed passwords), every role, timetable -> attendance -> student pages ->
overrides -> reports -> audit log. Fictional people only. No camera is ever opened: the
"camera ran" fact is a synthetic camera_runs row, exactly what a real run leaves behind.
Every response is scanned for passwords and password hashes.
"""
import base64
import json
from datetime import date, datetime, timedelta

import pytest
from jose import jwt

from app import models
from app.config import settings
from app.security import get_password_hash
from helpers import ASGIClient, utc_naive

DAY = date.today() - timedelta(days=date.today().weekday() + 14)  # a Monday, well in the past
PASSWORDS = {"root": "root-pass-0001", "opsy": "opsy-pass-0002", "vera": "vera-pass-0003",
             "asha_s": "asha-pass-0004", "ravi_s": "ravi-pass-0005"}


class Recorder(ASGIClient):
    """ASGIClient that fails the test if any response contains a password or a bcrypt hash"""

    def request(self, method, url, **kwargs):
        response = super().request(method, url, **kwargs)
        body = response.text
        leaked = [name for name, pw in PASSWORDS.items() if pw in body]
        assert not leaked, f"{method} {url} echoed a password ({leaked})"
        assert "$2b$" not in body and "$2a$" not in body, f"{method} {url} returned a password hash"
        return response


def at(hh, mm, day=DAY):
    return datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)


@pytest.fixture
def world(api):
    """Admin 'root' (real password), signed in through /auth/login. Returns helpers."""
    from app.main import app
    _, factory = api
    db = factory()
    db.add(models.User(email="root@college.edu", username="root", role="admin", is_active=True,
                       hashed_password=get_password_hash(PASSWORDS["root"])))
    db.commit()
    db.close()
    anonymous = Recorder(app)

    def sign_in(username, password=None):
        r = anonymous.post("/auth/login", data={"username": username, "password": password or PASSWORDS[username]})
        assert r.status_code == 200, r.text
        return Recorder(app, headers={"Authorization": f"Bearer {r.json()['access_token']}"})

    return {"app": app, "factory": factory, "anonymous": anonymous, "sign_in": sign_in, "admin": sign_in("root")}


def ok(response, status=200):
    assert response.status_code == status, f"{response.status_code}: {response.text}"
    return response.json() if "json" in response.headers.get("content-type", "") else response


@pytest.fixture
def school(world):
    """Through the API: classroom, IN camera (never started), two students, two Monday periods
    (backdated as if entered 30 days earlier) and a synthetic camera run covering DAY morning."""
    admin, factory = world["admin"], world["factory"]
    room = ok(admin.post("/timetable/classrooms", json={"name": "Room 204"}))
    camera = ok(admin.post("/cameras/", json={"name": "Door 204 IN", "source": "0", "source_type": "webcam",
                                              "camera_type": "IN", "classroom_id": room["id"]}))
    students = {sid: ok(admin.post("/students/", json={"student_id": sid, "name": name, "class_name": "CSE-B"}))
                for sid, name in (("RA9001", "Asha Test"), ("RA9002", "Ravi Test"))}
    periods = {}
    for code, name, start, end in (("CS101", "Data Structures", "09:00", "10:00"), ("CS102", "Operating Systems", "10:15", "11:15")):
        periods[code] = ok(admin.post("/timetable/periods", json={
            "classroom_id": room["id"], "class_name": "CSE-B", "course_code": code, "course_name": name,
            "day_of_week": 0, "start_time": start, "end_time": end, "grace_minutes": 10, "is_active": True}))
    db = factory()
    db.query(models.TimetablePeriod).update({"created_at": utc_naive(DAY - timedelta(days=30), datetime.min.time())})
    db.add(models.CameraRun(camera_id=camera["id"], started_at=utc_naive(DAY, at(8, 30).time()),
                            stopped_at=utc_naive(DAY, at(12, 0).time()), feed_kind="live"))
    db.commit()
    db.close()
    return {**world, "room": room, "camera": camera, "students": students, "periods": periods}


def manual(client, student, event_type, when):
    return client.post("/attendance/manual", json={"student_id": student["id"], "event_type": event_type,
                                                   "timestamp": when.isoformat(), "reason": "Fixture event"})


# --- A. admin-created accounts get exactly their role's permissions ---------------------

def test_a_admin_creates_staff_accounts_and_each_gets_only_its_role(world):
    admin = world["admin"]
    for username, role in (("opsy", "operator"), ("vera", "viewer")):
        ok(admin.post("/users/", json={"username": username, "email": f"{username}@college.edu",
                                       "password": PASSWORDS[username], "role": role}), 201)
    operator, viewer = world["sign_in"]("opsy"), world["sign_in"]("vera")
    assert ok(operator.get("/auth/me"))["role"] == "operator" and ok(viewer.get("/auth/me"))["role"] == "viewer"
    student = {"student_id": "RA9100", "name": "Op Made", "class_name": "CSE-B"}
    assert viewer.get("/students/").status_code == 200
    assert viewer.post("/students/", json=student).status_code == 403
    assert operator.post("/students/", json=student).status_code == 200
    for client in (operator, viewer):
        assert client.get("/users/").status_code == 403
        assert client.post("/users/", json={"username": "sneaky", "email": "s@college.edu",
                                            "password": "sneaky-pass-1", "role": "admin"}).status_code == 403
        assert client.post("/cameras/", json={"name": "X", "source": "1", "source_type": "webcam",
                                              "camera_type": "IN"}).status_code == 403
        assert client.post("/timetable/holidays", json={"date": str(DAY), "name": "X"}).status_code == 403
    me = ok(viewer.get("/auth/me"))
    assert viewer.patch(f"/users/{me['id']}", json={"role": "admin"}).status_code == 403  # no self-promotion


# --- B/C. linked student accounts see only their own record ----------------------------

def test_b_c_students_see_only_themselves_and_every_other_route_refuses_them(school):
    admin, students = school["admin"], school["students"]
    for username, sid in (("asha_s", "RA9001"), ("ravi_s", "RA9002")):
        ok(admin.post("/users/", json={"username": username, "email": f"{username}@college.edu",
                                       "password": PASSWORDS[username], "role": "student",
                                       "student_db_id": students[sid]["id"]}), 201)
    asha, ravi = school["sign_in"]("asha_s"), school["sign_in"]("ravi_s")
    asha_id = students["RA9001"]["id"]
    assert ok(asha.get("/portal/me/attendance"))["student"]["student_id"] == "RA9001"
    assert ok(ravi.get(f"/portal/me/attendance?student_db_id={asha_id}"))["student"]["student_id"] == "RA9002"
    period = school["periods"]["CS101"]["id"]
    denied = [
        ("get", f"/portal/students/{asha_id}/attendance", None), ("get", f"/students/{asha_id}", None),
        ("get", "/students/", None), ("get", f"/attendance/sessions?start_date={DAY}&end_date={DAY}&student_id={asha_id}", None),
        ("get", f"/attendance/student/{asha_id}/today", None), ("get", f"/reports/daily.csv?date={DAY}", None),
        ("get", f"/face-enrollment/{asha_id}", None), ("get", "/cameras/", None),
        ("get", f"/video/{school['camera']['id']}/frame", None), ("post", f"/video/{school['camera']['id']}/start", None),
        ("get", "/users/", None), ("get", "/users/audit-log", None), ("get", "/dashboard/stats", None),
        ("post", "/attendance/manual", {"student_id": asha_id, "event_type": "ENTRY", "timestamp": at(9, 0).isoformat(), "reason": "x" * 5}),
        ("put", f"/timetable/periods/{period}/attendance/{asha_id}?date={DAY}", {"status": "PRESENT", "reason": "self-help"}),
        ("patch", f"/users/{asha_id}", {"is_active": False}),
    ]
    for method, url, body in denied:
        r = getattr(ravi, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, f"student got {r.status_code} on {method.upper()} {url}"


def test_c_forged_expired_and_garbage_tokens_are_rejected(school):
    app, admin = school["app"], school["admin"]
    ok(admin.post("/users/", json={"username": "ravi_s", "email": "ravi_s@college.edu", "password": PASSWORDS["ravi_s"],
                                   "role": "student", "student_db_id": school["students"]["RA9002"]["id"]}), 201)
    secret = settings.SECRET_KEY.get_secret_value()
    future = datetime.utcnow() + timedelta(minutes=10)
    tokens = {
        "signed with another key": jwt.encode({"sub": "root", "exp": future}, "not-the-server-key", algorithm="HS256"),
        "expired": jwt.encode({"sub": "root", "exp": datetime.utcnow() - timedelta(minutes=1)}, secret, algorithm="HS256"),
        "alg none": ".".join(base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=") for part in
                             ({"alg": "none", "typ": "JWT"}, {"sub": "root", "exp": int(future.timestamp())})) + ".",
        "signature stripped": jwt.encode({"sub": "root", "exp": future}, secret, algorithm="HS256").rsplit(".", 1)[0] + ".",
        "garbage": "not.a.token",
        "unknown user": jwt.encode({"sub": "nobody", "exp": future}, secret, algorithm="HS256"),
    }
    for label, token in tokens.items():
        client = Recorder(app, headers={"Authorization": f"Bearer {token}"})
        for url in ("/users/", "/portal/me/attendance", "/auth/me"):
            assert client.get(url).status_code == 401, f"{label} token accepted on {url}"
    # A student who edits the username inside their token breaks the signature
    real = ok(school["anonymous"].post("/auth/login", data={"username": "ravi_s", "password": PASSWORDS["ravi_s"]}))["access_token"]
    header, payload, signature = real.split(".")
    forged_payload = jwt.encode({"sub": "root", "exp": future}, "x", algorithm="HS256").split(".")[1]
    forged = Recorder(app, headers={"Authorization": f"Bearer {header}.{forged_payload}.{signature}"})
    assert forged.get("/users/").status_code == 401
    assert school["anonymous"].post("/auth/login", data={"username": "ravi_s", "password": "wrong-pass-999"}).status_code == 401


# --- D/E. attendance numbers, history, boundaries, overrides, audit ----------------------

def test_d_e_course_wise_attendance_history_overrides_and_audit(school):
    admin, s = school["admin"], school["students"]
    ok(admin.post("/users/", json={"username": "opsy", "email": "opsy@college.edu", "password": PASSWORDS["opsy"], "role": "operator"}), 201)
    ok(admin.post("/users/", json={"username": "vera", "email": "vera@college.edu", "password": PASSWORDS["vera"], "role": "viewer"}), 201)
    operator, viewer = school["sign_in"]("opsy"), school["sign_in"]("vera")
    ok(manual(operator, s["RA9001"], "ENTRY", at(8, 55)))   # Asha: on time for CS101
    ok(manual(operator, s["RA9001"], "EXIT", at(10, 5)))    # ...leaves, misses CS102
    ok(manual(operator, s["RA9002"], "ENTRY", at(9, 25)))   # Ravi: late for CS101, stays
    assert viewer.post("/attendance/manual", json={"student_id": s["RA9001"]["id"], "event_type": "ENTRY",
                                                   "timestamp": at(9, 0).isoformat(), "reason": "viewer try"}).status_code == 403
    assert manual(operator, s["RA9001"], "ENTRY", datetime.now() + timedelta(hours=2)).status_code == 400  # no future events

    def courses(student):
        body = ok(admin.get(f"/portal/students/{student['id']}/attendance?start_date={DAY}&end_date={DAY}"))
        return {c["course_code"]: c for c in body["courses"]}, body["totals"]

    asha, totals = courses(s["RA9001"])
    assert (asha["CS101"]["attended"], asha["CS101"]["average_pct"]) == (1, 100.0)
    assert (asha["CS102"]["absent"], asha["CS102"]["average_pct"]) == (1, 0.0)
    assert totals["total_pct"] == 50.0
    ravi, totals = courses(s["RA9002"])
    assert (ravi["CS101"]["late"], ravi["CS101"]["attended"], ravi["CS102"]["attended"]) == (1, 1, 1)
    assert totals["total_pct"] == 100.0

    period_view = ok(admin.get(f"/timetable/periods/{school['periods']['CS102']['id']}/attendance?date={DAY}"))
    asha_row = next(r for r in period_view["students"] if r["student_id"] == "RA9001")
    assert asha_row["status"] == "ABSENT" and asha_row["exceptions"][0]["type"] == "DID_NOT_RETURN_AFTER_BREAK"

    history = ok(admin.get(f"/attendance/sessions?start_date={DAY}&end_date={DAY}&student_id={s['RA9001']['id']}"))
    assert len(history) == 1 and history[0]["duration_minutes"] == 70 and history[0]["status"] == "complete"

    # E. an operator excuses Asha's missed period; viewers cannot; the audit log records both directions
    url = f"/timetable/periods/{school['periods']['CS102']['id']}/attendance/{s['RA9001']['id']}?date={DAY}"
    assert viewer.put(url, json={"status": "EXCUSED", "reason": "Medical certificate"}).status_code == 403
    ok(operator.put(url, json={"status": "EXCUSED", "reason": "Medical certificate"}))
    asha, totals = courses(s["RA9001"])
    assert (asha["CS102"]["od_ml"], asha["CS102"]["od_ml_pct"], totals["total_pct"]) == (1, 100.0, 100.0)
    ok(operator.delete(url))
    assert courses(s["RA9001"])[1]["total_pct"] == 50.0
    actions = [e["action"] for e in ok(admin.get("/users/audit-log?limit=50"))]
    for needed in ("attendance.override_cleared", "attendance.override", "attendance.manual_event", "period.create",
                   "student.create", "camera.create", "classroom.create", "user.create"):
        assert needed in actions, f"{needed} missing from the audit log"
    override = next(e for e in ok(admin.get("/users/audit-log?limit=50")) if e["action"] == "attendance.override")
    assert override["username"] == "opsy" and override["details"]["after"]["status"] == "EXCUSED"


def test_d_holidays_unverified_and_future_classes_are_not_absences(school):
    admin, s = school["admin"], school["students"]
    next_monday = DAY + timedelta(days=7)
    ok(admin.post("/timetable/holidays", json={"date": str(next_monday), "name": "Fixture holiday"}))
    body = ok(admin.get(f"/portal/students/{s['RA9001']['id']}/attendance?start_date={DAY}&end_date={DAY + timedelta(days=13)}"))
    # DAY had a camera running and no entry -> 2 absences. next_monday is a holiday (not counted).
    # DAY+14 is outside the range. No other Mondays.
    assert (body["totals"]["max_hours"], body["totals"]["absent"], body["totals"]["unverified"]) == (2, 2, 0)
    # Asking past today never counts days that have not happened
    future = ok(admin.get(f"/portal/students/{s['RA9001']['id']}/attendance?start_date={date.today()}"
                          f"&end_date={date.today() + timedelta(days=60)}"))
    assert future["end_date"] == str(date.today())
    assert all(c["max_hours"] == 0 for c in future["courses"]) or future["totals"]["absent"] == 0
    # A Monday with no camera running is UNVERIFIED, not absent
    two_weeks_on = ok(admin.get(f"/portal/students/{s['RA9001']['id']}/attendance?start_date={DAY + timedelta(days=14)}"
                                f"&end_date={DAY + timedelta(days=14)}"))
    assert two_weeks_on["totals"]["absent"] == 0 and two_weeks_on["totals"]["unverified"] == 2


def test_d_period_cannot_be_viewed_before_it_existed(school):
    admin = school["admin"]
    r = admin.get(f"/timetable/periods/{school['periods']['CS101']['id']}/attendance?date={DAY - timedelta(days=35)}")
    assert r.status_code == 400


# --- F. reports, summaries and dashboard return real data ---------------------------------

def test_f_reports_summaries_and_dashboard(school):
    admin, s = school["admin"], school["students"]
    ok(manual(admin, s["RA9001"], "ENTRY", at(8, 55)))
    daily = admin.get(f"/reports/daily.csv?date={DAY}")
    assert daily.status_code == 200 and daily.headers["content-type"] == "text/csv; charset=utf-8"  # one charset only
    assert "attachment" in daily.headers.get("content-disposition", "")
    rows = daily.text.lstrip("﻿").splitlines()  # per-period summary: roster 2, Asha present all morning
    assert rows[0].startswith("Start,End,Class,Course,Roster,Present")
    assert rows[1].startswith("09:00,10:00,CSE-B,Data Structures,2,1,0,1") and len(rows) == 3
    monthly = admin.get(f"/reports/monthly.csv?year={DAY.year}&month={DAY.month}")
    assert monthly.status_code == 200 and "RA9001" in monthly.text
    sessions = admin.get(f"/reports/sessions.csv?start_date={DAY}&end_date={DAY}")
    assert sessions.status_code == 200 and "RA9001" in sessions.text
    for url in (f"/reports/daily.pdf?date={DAY}", f"/reports/sessions.pdf?start_date={DAY}&end_date={DAY}",
                f"/reports/monthly.pdf?year={DAY.year}&month={DAY.month}"):
        pdf = admin.get(url)
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF", url
    summary = ok(admin.get(f"/timetable/daily?date={DAY}"))
    assert {p["course_code"] for p in summary["periods"]} == {"CS101", "CS102"}
    month = ok(admin.get(f"/timetable/monthly?year={DAY.year}&month={DAY.month}&class_name=CSE-B"))
    assert {r["student_id"] for r in month["students"]} == {"RA9001", "RA9002"}
    stats = ok(admin.get("/dashboard/stats"))
    assert stats["total_students"] == 2 and stats["total_cameras"] == 1


# --- G. errors, empty data, invalid input, duplicates ---------------------------------------

def test_g_errors_duplicates_invalid_input_and_empty_data(school):
    admin, s = school["admin"], school["students"]
    assert admin.post("/students/", json={"student_id": "RA9001", "name": "Dup"}).status_code in (400, 409)
    assert admin.post("/students/", json={"student_id": "  ", "name": "Blank"}).status_code == 422
    assert admin.post("/timetable/classrooms", json={"name": "Room 204"}).status_code in (400, 409)
    bad_period = {"classroom_id": school["room"]["id"], "class_name": "CSE-B", "course_name": "Backwards",
                  "day_of_week": 0, "start_time": "11:00", "end_time": "10:00"}
    assert admin.post("/timetable/periods", json=bad_period).status_code in (400, 422)
    overlap = {**bad_period, "course_name": "Overlap", "start_time": "09:30", "end_time": "10:30"}
    assert admin.post("/timetable/periods", json=overlap).status_code == 409
    assert admin.post("/users/", json={"username": "ROOT", "email": "x@college.edu", "password": "longenough1"}).status_code == 400
    assert admin.post("/users/", json={"username": "fresh1", "email": "ROOT@college.edu", "password": "longenough1"}).status_code == 400
    assert admin.get(f"/portal/students/{s['RA9001']['id']}/attendance?start_date={DAY}&end_date={DAY - timedelta(days=1)}").status_code == 400
    assert admin.get("/portal/students/99999/attendance").status_code == 404
    assert admin.get(f"/attendance/sessions?start_date={DAY}&end_date={DAY - timedelta(days=1)}").status_code in (400, 422)
    # A student with no timetable gets an empty page, not an error
    loner = ok(admin.post("/students/", json={"student_id": "RA9999", "name": "No Class"}))
    empty = ok(admin.get(f"/portal/students/{loner['id']}/attendance"))
    assert empty["courses"] == [] and empty["totals"]["max_hours"] == 0
    for url in ("/users/", "/students/", "/portal/me/attendance", "/reports/daily.csv?date=2026-10-01"):
        assert school["anonymous"].get(url).status_code in (401, 403)
    assert school["anonymous"].post("/auth/register", json={"username": "walkin", "email": "w@college.edu",
                                                            "password": "walkin-pass-1"}).status_code == 403


# --- H. camera and recognition paths without devices ----------------------------------------

def test_h_camera_credentials_masked_and_no_device_opened_by_permission_checks(school, monkeypatch):
    """Refused start requests must be rejected before any capture device is touched"""
    import cv2
    opened = []
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: opened.append(a) or pytest.fail("a capture device was opened"))
    admin = school["admin"]
    cam = ok(admin.post("/cameras/", json={"name": "Gate RTSP", "source": "rtsp://guard:hunter2@10.0.0.9/stream",
                                           "source_type": "rtsp", "camera_type": "OUT"}))
    assert "hunter2" not in str(cam) and "hunter2" not in admin.get("/cameras/").text
    assert "hunter2" not in admin.get("/users/audit-log").text
    ok(admin.post("/users/", json={"username": "vera", "email": "vera@college.edu", "password": PASSWORDS["vera"], "role": "viewer"}), 201)
    viewer = school["sign_in"]("vera")
    for url in (f"/video/{cam['id']}/start", f"/cameras/{cam['id']}/test", f"/cameras/{cam['id']}/toggle"):
        assert viewer.post(url).status_code == 403
    assert school["anonymous"].post(f"/video/{cam['id']}/start").status_code in (401, 403)
    assert opened == []
    status = ok(admin.get(f"/video/{cam['id']}/status"))
    assert status.get("active") in (False, None) or status.get("running") in (False, None)
