"""
Student accounts and course-wise attendance (/portal). A student account must see only
its own record and be refused by every staff endpoint. All data is in an isolated SQLite file.
"""
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute

from app import models
from app.security import create_access_token
from helpers import ASGIClient, add_event
from test_period_attendance import DAY, add_run, at, school  # noqa: F401  (school is a fixture)


def student_client(factory, username="asha", student_db_id=None):
    from app.main import app
    db = factory()
    db.add(models.User(email=f"{username}@college.edu", username=username, hashed_password="unused",
                       role="student", student_db_id=student_db_id))
    db.commit()
    db.close()
    return ASGIClient(app, headers={"Authorization": f"Bearer {create_access_token({'sub': username})}"})


def courses(body):
    return {c["course_name"]: c for c in body["courses"]}


def test_course_wise_hours_and_percentages(school):
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(8, 55), ids["in"])   # Maths: present
    add_event(db, ids["A1"], "EXIT", at(10, 5), ids["out"])   # Physics: left, never came back -> absent
    db.close()
    r = client.get(f"/portal/students/{ids['A1']}/attendance?start_date={DAY}&end_date={DAY}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["student"]["student_id"] == "A1" and body["student"]["class_name"] == "CSE-A"
    maths, physics = courses(body)["Maths"], courses(body)["Physics"]
    assert (maths["max_hours"], maths["attended"], maths["absent"], maths["average_pct"]) == (1, 1, 0, 100.0)
    assert (physics["max_hours"], physics["attended"], physics["absent"], physics["average_pct"]) == (1, 0, 1, 0.0)
    assert body["totals"] == {"max_hours": 2, "attended": 1, "late": 0, "absent": 1, "od_ml": 0,
                              "unverified": 0, "total_pct": 50.0}


def test_excused_override_counts_as_od_ml_and_towards_total(school):
    client, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    db.add(models.AttendanceOverride(period_id=ids["p1"], date=DAY, student_id=ids["A2"], status="EXCUSED",
                                     reason="Sports meet (OD)"))
    db.commit()
    db.close()
    maths = courses(client.get(f"/portal/students/{ids['A2']}/attendance?start_date={DAY}&end_date={DAY}").json())["Maths"]
    assert (maths["od_ml"], maths["average_pct"], maths["od_ml_pct"], maths["total_pct"]) == (1, 0.0, 100.0, 100.0)


def test_periods_without_a_running_camera_are_not_counted_against_the_student(school):
    client, _, ids = school
    body = client.get(f"/portal/students/{ids['A3']}/attendance?start_date={DAY}&end_date={DAY}").json()
    assert body["totals"]["max_hours"] == 0 and body["totals"]["unverified"] == 2
    assert all(c["average_pct"] is None for c in body["courses"])


def test_student_sees_only_their_own_record(school):
    _, factory, ids = school
    db = factory()
    add_run(db, ids["in"], at(8, 30), at(12, 0))
    add_event(db, ids["A1"], "ENTRY", at(8, 55), ids["in"])
    db.close()
    student = student_client(factory, student_db_id=ids["A1"])
    r = student.get(f"/portal/me/attendance?start_date={DAY}&end_date={DAY}&student_db_id={ids['A2']}")
    assert r.status_code == 200 and r.json()["student"]["student_id"] == "A1"  # the extra parameter is ignored
    assert student.get(f"/portal/students/{ids['A2']}/attendance").status_code == 403
    assert student.get(f"/portal/students/{ids['A1']}/attendance").status_code == 403  # staff view, even of self
    assert student.get("/auth/me").json()["role"] == "student"


def test_unlinked_student_account_and_staff_on_the_student_endpoint(school):
    client, factory, _ = school
    assert student_client(factory).get("/portal/me/attendance").status_code == 404
    assert client.get("/portal/me/attendance").status_code == 403  # staff have no personal page


def test_deleted_student_record_is_not_shown(school):
    _, factory, ids = school
    student = student_client(factory, student_db_id=ids["A1"])
    db = factory()
    db.query(models.Student).filter_by(id=ids["A1"]).update({"deleted_at": at(8, 0)})
    db.commit()
    db.close()
    assert student.get("/portal/me/attendance").status_code == 404


def test_date_range_is_validated(school):
    client, _, ids = school
    url = f"/portal/students/{ids['A1']}/attendance"
    assert client.get(f"{url}?start_date={DAY}&end_date={DAY - timedelta(days=1)}").status_code == 400
    assert client.get(f"{url}?start_date={DAY - timedelta(days=400)}&end_date={DAY}").status_code == 400
    default = client.get(url).json()  # from the day the timetable was added, to today
    assert default["start_date"] == str(DAY - timedelta(days=30)) and default["courses"]


PUBLIC = {"/", "/health", "/auth/login", "/auth/token", "/auth/register", "/auth/registration"}
STUDENT_ALLOWED = {"/auth/me", "/portal/me/attendance"}


def test_student_account_is_refused_by_every_staff_endpoint(school):
    """Walks every API route, so a future endpoint that forgets its role check fails here"""
    from app.main import app
    _, factory, ids = school
    student = student_client(factory, student_db_id=ids["A1"])
    checked = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC | STUDENT_ALLOWED:
            continue
        path = route.path
        for name in route.param_convertors:
            path = path.replace("{" + name + "}", "1")
        for method in route.methods - {"HEAD"}:
            r = getattr(student, method.lower())(path)
            assert r.status_code in (401, 403), f"student got {r.status_code} on {method} {route.path}"
            checked.append(f"{method} {route.path}")
    assert len(checked) > 40  # sanity: the walk really covered the API


def new_student_account(student_db_id, **extra):
    return {"username": "asha_s", "email": "asha.s@college.edu", "password": "initial-pass-1", "role": "student",
            "student_db_id": student_db_id, **extra}


def test_admin_creates_a_linked_student_account_that_signs_in_to_its_own_page(school):
    from app.main import app
    client, factory, ids = school
    r = client.post("/users/", json=new_student_account(ids["A1"]))
    assert r.status_code == 201, r.text
    assert (r.json()["role"], r.json()["student_db_id"]) == ("student", ids["A1"])
    entry = client.get("/users/audit-log").json()[0]
    assert entry["action"] == "user.create" and entry["details"] == {"username": "asha_s", "role": "student", "student_id": "A1"}
    assert "initial-pass-1" not in str(entry)

    token = ASGIClient(app).post("/auth/login", data={"username": "asha_s", "password": "initial-pass-1"}).json()["access_token"]
    student = ASGIClient(app, headers={"Authorization": f"Bearer {token}"})
    assert student.get("/auth/me").json()["student_db_id"] == ids["A1"]
    assert student.get("/portal/me/attendance").json()["student"]["student_id"] == "A1"
    assert student.get("/students/").status_code == 403


@pytest.mark.parametrize("body,status,detail", [
    ({"role": "student", "student_db_id": None}, 422, None),          # student without a record
    ({"role": "viewer"}, 422, None),                                   # staff with a record
    ({"student_db_id": 9999}, 400, "existing"),                         # no such student
    ({"student_db_id": "staff"}, 400, "existing"),                      # a staff record, not a student
    ({"student_db_id": "deleted"}, 400, "existing"),                    # a deleted student
])
def test_student_account_must_link_one_current_student(school, body, status, detail):
    client, factory, ids = school
    db = factory()
    db.query(models.Student).filter_by(id=ids["A3"]).update({"deleted_at": at(8, 0)})
    db.commit()
    db.close()
    body = {**body, "student_db_id": {"staff": ids["staff"], "deleted": ids["A3"]}.get(body.get("student_db_id"),
                                                                                      body.get("student_db_id", ids["A1"]))}
    r = client.post("/users/", json=new_student_account(**body) if "student_db_id" in body else new_student_account(ids["A1"], **body))
    assert r.status_code == status, r.text
    if detail:
        assert detail in r.json()["detail"]
    db = factory()
    assert db.query(models.User).filter_by(username="asha_s").count() == 0
    db.close()


def test_one_account_per_student(school):
    client, _, ids = school
    assert client.post("/users/", json=new_student_account(ids["A1"])).status_code == 201
    r = client.post("/users/", json=new_student_account(ids["A1"], username="asha_two", email="two@college.edu"))
    assert r.status_code == 409 and "A1 already has an account" in r.json()["detail"]


def test_student_account_can_be_deactivated_but_never_given_a_staff_role(school):
    from app.main import app
    client, _, ids = school
    account = client.post("/users/", json=new_student_account(ids["A1"])).json()
    for role in ("viewer", "operator", "admin"):
        r = client.patch(f"/users/{account['id']}", json={"role": role})
        assert r.status_code == 400 and "staff role" in r.json()["detail"]
    token = ASGIClient(app).post("/auth/login", data={"username": "asha_s", "password": "initial-pass-1"}).json()["access_token"]
    assert client.patch(f"/users/{account['id']}", json={"is_active": False}).status_code == 200
    assert ASGIClient(app, headers={"Authorization": f"Bearer {token}"}).get("/portal/me/attendance").status_code == 401


def test_only_admins_create_student_accounts(school):
    client, factory, ids = school
    db = factory()
    db.add(models.User(email="op@x.org", username="op", hashed_password="unused", role="operator"))
    db.commit()
    db.close()
    from app.main import app
    operator = ASGIClient(app, headers={"Authorization": f"Bearer {create_access_token({'sub': 'op'})}"})
    assert operator.post("/users/", json=new_student_account(ids["A1"])).status_code == 403
    student = student_client(factory, username="s2", student_db_id=ids["A2"])
    assert student.post("/users/", json=new_student_account(ids["A1"])).status_code == 403


@pytest.mark.parametrize("url,body", [
    ("/users/", {"username": "asha1", "email": "asha1@college.edu", "password": "Secret-Pass-77", "role": "student"}),
    ("/users/", {"username": "asha1", "password": "Secret-Pass-77"}),  # email missing
    ("/auth/register", {"username": "asha1", "password": "Secret-Pass-77"}),
])
def test_body_level_validation_errors_never_echo_the_password(api, registration_open, url, body):
    """A whole-body or missing-field error carries the whole submitted body as its input"""
    client, _ = api
    r = client.post(url, json=body)
    assert r.status_code == 422
    assert "Secret-Pass-77" not in r.text and r.json()["detail"]


@pytest.mark.parametrize("change", [{"role": "student"}, {"role": "superuser"}])
def test_staff_accounts_cannot_be_switched_to_student_by_role_change(api, change):
    client, factory = api
    db = factory()
    db.add(models.User(email="v@example.com", username="viewer1", hashed_password="unused", role="viewer"))
    db.commit()
    user_id = db.query(models.User).filter_by(username="viewer1").one().id
    db.close()
    assert client.patch(f"/users/{user_id}", json=change).status_code == 422
