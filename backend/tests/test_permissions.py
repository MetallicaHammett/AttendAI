"""
Server-side role enforcement (viewer < operator < admin), safe self-registration,
user administration and the audit log.
"""
import pytest

from app import models
from app.security import create_access_token, get_password_hash
from helpers import ASGIClient, add_camera, add_student


@pytest.fixture
def clients(api):
    """(admin_client, {role: client}, factory) with one account per role"""
    admin, factory = api
    db = factory()
    for role in ("viewer", "operator"):
        db.add(models.User(email=f"{role}@example.com", username=role, hashed_password="unused", role=role))
    db.commit()
    db.close()
    from app.main import app
    by_role = {role: ASGIClient(app, headers={"Authorization": f"Bearer {create_access_token({'sub': role})}"})
               for role in ("viewer", "operator")}
    by_role["admin"] = admin
    return by_role, factory


STUDENT = {"student_id": "P1", "name": "Priya"}
CAMERA = {"name": "Gate", "source": "1", "source_type": "webcam", "camera_type": "IN"}


@pytest.mark.parametrize("method,url,body,minimum", [
    ("get", "/students/", None, "viewer"),
    ("get", "/dashboard/stats", None, "viewer"),
    ("get", "/video/live", None, "viewer"),
    ("post", "/students/", STUDENT, "operator"),
    ("post", "/cameras/", CAMERA, "admin"),
    ("post", "/timetable/classrooms", {"name": "R1"}, "admin"),
    ("get", "/users/", None, "admin"),
    ("get", "/users/audit-log", None, "admin"),
])
def test_role_matrix(clients, method, url, body, minimum):
    by_role, _ = clients
    rank = {"viewer": 0, "operator": 1, "admin": 2}
    for role, client in by_role.items():
        kwargs = {"json": body} if body is not None else {}
        response = getattr(client, method)(url, **kwargs)
        if rank[role] >= rank[minimum]:
            assert response.status_code != 403, f"{role} should be allowed {method.upper()} {url}"
        else:
            assert response.status_code == 403, f"{role} must not {method.upper()} {url}"
            assert minimum in response.json()["detail"]


def test_viewer_cannot_start_cameras_or_delete_records(clients):
    by_role, factory = clients
    db = factory()
    camera_id = add_camera(db).id
    student_id = add_student(db, "S1").id
    db.close()
    viewer = by_role["viewer"]
    assert viewer.post(f"/video/{camera_id}/start").status_code == 403
    assert viewer.delete(f"/students/{student_id}").status_code == 403
    assert by_role["operator"].delete(f"/students/{student_id}").status_code == 403  # deleting is admin-only
    assert viewer.post(f"/face-enrollment/{student_id}", files={"file": ("f.png", b"x", "image/png")}).status_code == 403


def test_unauthenticated_requests_are_rejected(api):
    from app.main import app
    anonymous = ASGIClient(app)
    for url in ("/students/", "/dashboard/stats", "/video/live", "/reports/daily.csv?date=2026-10-19"):
        assert anonymous.get(url).status_code in (401, 403)


def test_self_registration_creates_viewer_and_ignores_role_in_request(api, registration_open):
    from app.main import app
    anonymous = ASGIClient(app)
    assert anonymous.get("/auth/registration").json() == {"enabled": True}
    r = anonymous.post("/auth/register", json={"email": "new@example.com", "username": "newbie",
                                               "password": "longenough1", "role": "admin"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "viewer"


def test_self_registration_can_be_disabled(api, registration_closed):
    from app import models
    from app.main import app
    client, factory = api
    anonymous = ASGIClient(app)
    assert anonymous.get("/auth/registration").json() == {"enabled": False}  # public, no token needed
    r = anonymous.post("/auth/register", json={"email": "x@example.com", "username": "xuser", "password": "longenough1"})
    assert r.status_code == 403 and "administrator" in r.json()["detail"]
    db = factory()
    assert db.query(models.User).filter_by(username="xuser").count() == 0  # nothing was created
    db.close()


def test_registration_is_closed_by_default_in_tests(api):
    """Matches the production configuration; sign-up tests must opt in explicitly"""
    from app.main import app
    assert ASGIClient(app).get("/auth/registration").json() == {"enabled": False}


def test_overlong_password_rejected_not_truncated(api, registration_open):
    from app.main import app
    r = ASGIClient(app).post("/auth/register", json={"email": "y@example.com", "username": "yuser", "password": "a" * 73})
    assert r.status_code == 422


def test_login_returns_role_through_me(api):
    _, factory = api
    db = factory()
    db.add(models.User(email="op@example.com", username="op", hashed_password=get_password_hash("correct-horse"),
                       role="operator"))
    db.commit()
    db.close()
    from app.main import app
    anonymous = ASGIClient(app)
    token = anonymous.post("/auth/login", data={"username": "op", "password": "correct-horse"}).json()["access_token"]
    me = ASGIClient(app, headers={"Authorization": f"Bearer {token}"}).get("/auth/me").json()
    assert me["role"] == "operator" and "hashed_password" not in me
    assert anonymous.post("/auth/login", data={"username": "op", "password": "wrong"}).status_code == 401


def test_admin_manages_roles_cannot_lock_self_out_and_changes_are_audited(clients):
    by_role, factory = clients
    admin = by_role["admin"]
    users = {u["username"]: u for u in admin.get("/users/").json()}
    assert {"admin", "viewer", "operator"} <= set(users)

    r = admin.patch(f"/users/{users['viewer']['id']}", json={"role": "operator"})
    assert r.status_code == 200 and r.json()["role"] == "operator"
    assert admin.patch(f"/users/{users['admin']['id']}", json={"role": "viewer"}).status_code == 400
    assert admin.patch(f"/users/{users['admin']['id']}", json={"is_active": False}).status_code == 400
    assert admin.patch(f"/users/{users['viewer']['id']}", json={"role": "superuser"}).status_code == 422

    log = admin.get("/users/audit-log").json()
    assert log[0]["action"] == "user.update" and log[0]["details"]["after"]["role"] == "operator"


@pytest.mark.parametrize("field,value", [
    ("email", "not-an-email"), ("email", "[a@b.com](mailto:a@b.com)"), ("email", "a@b"), ("email", "a b@c.com"),
    ("username", "ab"), ("username", "has space"), ("username", "<script>"), ("username", "a" * 51),
])
def test_registration_rejects_malformed_username_or_email(api, registration_open, field, value):
    from app.main import app
    body = {"email": "valid@example.com", "username": "validname", "password": "longenough1", field: value}
    assert ASGIClient(app).post("/auth/register", json=body).status_code == 422
    _, factory = api
    db = factory()
    assert db.query(models.User).count() == 1  # only the fixture admin
    db.close()


@pytest.mark.parametrize("username,email,detail", [
    ("ADMIN", "fresh@example.com", "Username"), ("fresh", "Admin@Example.COM", "Email")])
def test_registration_uniqueness_ignores_letter_case(api, registration_open, username, email, detail):
    from app.main import app
    r = ASGIClient(app).post("/auth/register", json={"email": email, "username": username, "password": "longenough1"})
    assert r.status_code == 400 and detail in r.json()["detail"]


def test_listing_still_works_with_legacy_malformed_email(clients):
    """Validation applies to new accounts only; old records must not break the admin page"""
    by_role, factory = clients
    db = factory()
    db.add(models.User(email="[x@y.com](mailto:x@y.com)", username="legacy", hashed_password="unused", role="viewer"))
    db.commit()
    db.close()
    r = by_role["admin"].get("/users/")
    assert r.status_code == 200 and "legacy" in {u["username"] for u in r.json()}


def test_operator_and_viewer_cannot_change_any_account_including_their_own(clients):
    by_role, _ = clients
    ids = {u["username"]: u["id"] for u in by_role["admin"].get("/users/").json()}
    for role in ("viewer", "operator"):
        client = by_role[role]
        assert client.patch(f"/users/{ids[role]}", json={"role": "admin"}).status_code == 403  # self-promotion
        assert client.patch(f"/users/{ids['admin']}", json={"is_active": False}).status_code == 403
        assert client.get("/users/").status_code == 403
    assert {u["username"]: u["role"] for u in by_role["admin"].get("/users/").json()} == \
        {"admin": "admin", "viewer": "viewer", "operator": "operator"}


def test_role_change_ignores_fields_other_than_role_and_active(clients):
    by_role, factory = clients
    admin = by_role["admin"]
    target = next(u for u in admin.get("/users/").json() if u["username"] == "viewer")
    r = admin.patch(f"/users/{target['id']}", json={"role": "operator", "username": "renamed", "email": "h@x.com",
                                                    "hashed_password": "attacker", "id": 999})
    assert r.status_code == 200
    db = factory()
    user = db.query(models.User).filter_by(id=target["id"]).one()
    assert (user.username, user.email, user.hashed_password, user.role) == \
        ("viewer", "viewer@example.com", "unused", "operator")
    db.close()


def test_admin_can_demote_another_admin_while_one_admin_remains(clients):
    by_role, factory = clients
    admin = by_role["admin"]
    ids = {u["username"]: u["id"] for u in admin.get("/users/").json()}
    assert admin.patch(f"/users/{ids['operator']}", json={"role": "admin"}).status_code == 200
    assert admin.patch(f"/users/{ids['operator']}", json={"role": "viewer"}).status_code == 200
    assert admin.patch(f"/users/{ids['viewer']}", json={"is_active": False}).status_code == 200
    assert admin.patch(f"/users/{ids['viewer']}", json={"is_active": True}).status_code == 200


def test_last_active_admin_cannot_be_demoted_or_deactivated(clients, monkeypatch):
    """Only reachable in a race (two admins demoting each other at once), so the moment the
    other admin's change has landed is simulated; nothing may be written."""
    from app import crud
    by_role, factory = clients
    admin = by_role["admin"]
    ids = {u["username"]: u["id"] for u in admin.get("/users/").json()}
    assert admin.patch(f"/users/{ids['operator']}", json={"role": "admin"}).status_code == 200
    audit_rows = len(admin.get("/users/audit-log").json())
    monkeypatch.setattr(crud, "count_other_active_admins", lambda db, excluding_user_id: 0)
    for change in ({"role": "operator"}, {"is_active": False}):
        r = admin.patch(f"/users/{ids['operator']}", json=change)
        assert r.status_code == 409 and "last active admin" in r.json()["detail"]
    db = factory()
    user = db.query(models.User).filter_by(id=ids["operator"]).one()
    assert (user.role, user.is_active) == ("admin", True)
    db.close()
    assert len(admin.get("/users/audit-log").json()) == audit_rows  # refused changes are not audited


NEW_ACCOUNT = {"username": "teacher1", "email": "teacher1@college.edu", "password": "initial-pass-1", "role": "operator"}


def test_admin_creates_account_with_role_even_when_registration_is_closed(clients, registration_closed):
    from app.security import verify_password
    by_role, factory = clients
    r = by_role["admin"].post("/users/", json=NEW_ACCOUNT)
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["username"], body["role"], body["is_active"]) == ("teacher1", "operator", True)
    assert "password" not in body and "hashed_password" not in body
    db = factory()
    user = db.query(models.User).filter_by(username="teacher1").one()
    assert verify_password("initial-pass-1", user.hashed_password)
    db.close()
    entry = by_role["admin"].get("/users/audit-log").json()[0]
    assert entry["action"] == "user.create" and entry["details"] == {"username": "teacher1", "role": "operator"}
    assert "initial-pass-1" not in str(entry) and "$2" not in str(entry)
    # The new account can sign in and has exactly the chosen role
    from app.main import app
    token = ASGIClient(app).post("/auth/login", data={"username": "teacher1", "password": "initial-pass-1"}).json()
    me = ASGIClient(app, headers={"Authorization": f"Bearer {token['access_token']}"}).get("/auth/me").json()
    assert me["role"] == "operator"


def test_created_account_defaults_to_viewer(clients):
    by_role, _ = clients
    body = {k: v for k, v in NEW_ACCOUNT.items() if k != "role"}
    assert by_role["admin"].post("/users/", json=body).json()["role"] == "viewer"


def test_operator_and_viewer_cannot_create_accounts(clients):
    by_role, factory = clients
    for role in ("viewer", "operator"):
        r = by_role[role].post("/users/", json={**NEW_ACCOUNT, "role": "admin"})
        assert r.status_code == 403
    from app.main import app
    assert ASGIClient(app).post("/users/", json=NEW_ACCOUNT).status_code in (401, 403)
    db = factory()
    assert db.query(models.User).filter_by(username="teacher1").count() == 0
    assert db.query(models.AuditLog).count() == 0
    db.close()


@pytest.mark.parametrize("changes,status", [
    ({"username": "OPERATOR"}, 400), ({"email": "Viewer@Example.com"}, 400),
    ({"username": "ab"}, 422), ({"email": "[a@b.com](mailto:a@b.com)"}, 422),
    ({"password": "short"}, 422), ({"password": "x" * 73}, 422), ({"role": "superuser"}, 422),
])
def test_admin_account_creation_rejects_invalid_or_duplicate_details(clients, changes, status):
    by_role, factory = clients
    db = factory()
    before = db.query(models.User).count()
    db.close()
    assert by_role["admin"].post("/users/", json={**NEW_ACCOUNT, **changes}).status_code == status
    db = factory()
    assert db.query(models.User).count() == before
    assert db.query(models.AuditLog).count() == 0
    db.close()


@pytest.mark.parametrize("url", ["/users/", "/auth/register"])
def test_duplicate_that_slips_past_the_check_is_a_clean_400_not_a_crash(clients, registration_open, monkeypatch, url):
    """Two requests for the same name at once both pass the pre-check; the database's
    unique constraint must then turn the second into a normal refusal, with nothing written."""
    from app import crud
    by_role, factory = clients
    monkeypatch.setattr(crud, "find_conflicting_user", lambda db, username, email: None)
    body = {"username": "operator", "email": "other@example.com", "password": "longenough1"}
    r = by_role["admin"].post(url, json=body)
    assert r.status_code == 400 and "already" in r.json()["detail"]
    db = factory()
    assert db.query(models.User).filter_by(username="operator").count() == 1
    assert db.query(models.AuditLog).count() == 0
    db.close()


@pytest.mark.parametrize("url", ["/users/", "/auth/register"])
def test_rejected_password_is_not_echoed_in_the_error(clients, registration_open, url):
    by_role, _ = clients
    secret = "Zq-not-echoed-" * 6  # 84 characters: over the 72-byte limit
    r = by_role["admin"].post(url, json={**NEW_ACCOUNT, "password": secret})
    assert r.status_code == 422
    assert secret not in r.text
    assert any(error["loc"][-1] == "password" for error in r.json()["detail"])  # still says which field
    # Other fields keep the standard error format, including the submitted value
    r = by_role["admin"].post(url, json={**NEW_ACCOUNT, "username": "a b"})
    assert r.status_code == 422 and r.json()["detail"][0]["input"] == "a b"


def test_deactivated_account_loses_access_immediately(clients):
    by_role, factory = clients
    db = factory()
    db.query(models.User).filter_by(username="operator").update({"is_active": False})
    db.commit()
    db.close()
    assert by_role["operator"].get("/students/").status_code == 401


def test_audit_log_never_stores_stream_credentials(clients):
    by_role, factory = clients
    camera = {"name": "CCTV", "source": "rtsp://admin:s3cret@10.0.0.5/stream1", "source_type": "rtsp", "camera_type": "IN"}
    r = by_role["admin"].post("/cameras/", json=camera)
    assert r.status_code == 200, r.text
    assert "s3cret" not in r.text
    db = factory()
    details = [row.details or "" for row in db.query(models.AuditLog).all()]
    db.close()
    assert details and not any("s3cret" in d for d in details)


@pytest.mark.parametrize("origin,allowed", [
    ("http://localhost:3000", True), ("http://127.0.0.1:3000", True), ("http://127.0.0.1:3001", True),
    ("http://evil.example", False), ("http://192.168.1.5:3000", False),
])
def test_cors_allows_only_local_frontend_origins(api, origin, allowed):
    client, _ = api
    r = client.request("OPTIONS", "/students/", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
    assert (r.headers.get("access-control-allow-origin") == origin) is allowed
