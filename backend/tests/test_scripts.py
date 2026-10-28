"""
Operator scripts: accuracy evaluation matching rules (synthetic labels - this checks the
scoring logic, it is not an accuracy result) and the user-administration CLI.
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)

import evaluate_accuracy as ev  # noqa: E402

T0 = datetime(2026, 10, 21, 9, 0, 0)


def c(person, event_type, seconds):
    return ev.Crossing(person, event_type, T0 + timedelta(seconds=seconds))


def test_evaluation_counts_each_crossing_once():
    truth = [c("S1", "ENTRY", 0), c("S2", "ENTRY", 5), c("S3", "ENTRY", 10), c("VISITOR-1", "ENTRY", 20)]
    system = [
        c("S1", "ENTRY", 2),     # matched, 2 s late
        c("S1", "ENTRY", 3),     # duplicate -> spurious, not a second match
        c("S9", "ENTRY", 11),    # S3 recognised as S9 -> wrong identity
        c("S4", "ENTRY", 21),    # visitor recognised as a student -> wrong identity
        c("S5", "EXIT", 300),    # nothing happened -> spurious
    ]
    report = ev.evaluate(truth, system, tolerance=10)
    assert report["matched"] == 1 and report["missed"] == 2  # S2 and S3 not recognised as themselves
    assert report["wrong_identity"] == 2 and report["spurious"] == 2
    assert report["identification_accuracy"] == round(1 / (3 + 2 + 2) * 100, 1)
    assert report["recall"] == round(1 / 3 * 100, 1) and report["precision"] == 20.0
    assert report["timing_seconds"]["max"] == 2.0


def test_evaluation_outside_tolerance_is_missed():
    report = ev.evaluate([c("S1", "EXIT", 0)], [c("S1", "EXIT", 30)], tolerance=10)
    assert report["matched"] == 0 and report["missed"] == 1 and report["spurious"] == 1


def test_db_is_opened_read_only(tmp_path):
    db = tmp_path / "events.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE students (id INTEGER, student_id TEXT);"
                      "CREATE TABLE attendance_events (student_id INTEGER, event_type TEXT, timestamp TEXT);"
                      "INSERT INTO students VALUES (1, 'S1');"
                      "INSERT INTO attendance_events VALUES (1, 'ENTRY', '2026-10-21 03:30:00');")
    con.commit()
    con.close()
    before = db.read_bytes()
    events = ev.read_db_events(str(db), datetime(2000, 1, 1), datetime(2100, 1, 1))
    assert [(e.person, e.event_type) for e in events] == [("S1", "ENTRY")]
    assert db.read_bytes() == before


def test_manual_corrections_never_count_as_camera_recognitions(tmp_path):
    """An operator's manual event for a missed student must not turn the miss into a match"""
    db = tmp_path / "events.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE students (id INTEGER, student_id TEXT);"
                      "CREATE TABLE attendance_events (student_id INTEGER, event_type TEXT, timestamp TEXT, source TEXT);"
                      "INSERT INTO students VALUES (1, 'S1'), (2, 'S2');")
    utc = lambda seconds: (T0 + timedelta(seconds=seconds)).astimezone().astimezone(
        ev.timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
    con.execute("INSERT INTO attendance_events VALUES (1, 'ENTRY', ?, 'camera')", (utc(1),))
    con.execute("INSERT INTO attendance_events VALUES (2, 'ENTRY', ?, 'manual')", (utc(4),))  # S2 corrected by hand
    con.commit()
    con.close()
    truth = tmp_path / "truth.csv"
    truth.write_text("person,event_type,timestamp,notes\n"
                     f"S1,ENTRY,{(T0).isoformat()},\nS2,ENTRY,{(T0 + timedelta(seconds=5)).isoformat()},\n", encoding="utf-8")
    ev.main(["--ground-truth", str(truth), "--db", str(db), "--out", str(tmp_path / "report")])
    import json
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert (report["matched"], report["missed"], report["manual_events_excluded"]) == (1, 1, 1)
    assert report["identification_accuracy"] == 50.0
    assert "operator corrections, not camera recognitions" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_report_states_false_positives_negatives_visitors_and_denominator():
    truth = [c("S1", "ENTRY", 0), c("S2", "ENTRY", 30), c("VISITOR-1", "ENTRY", 60), c("VISITOR-2", "ENTRY", 90)]
    system = [c("S1", "ENTRY", 1), c("S7", "ENTRY", 61), c("S8", "EXIT", 500)]  # visitor 1 taken for S7
    report = ev.evaluate(truth, system, tolerance=10)
    assert (report["false_negatives"], report["false_positives"]) == (1, 2)
    assert (report["visitors_recorded_as_students"], report["visitor_crossings_correctly_not_recorded"]) == (1, 1)
    assert report["denominator"] == 2 + 1 + 1 and report["identification_accuracy"] == 25.0
    text = ev.to_markdown(report)
    assert "matched / (enrolled crossings + wrong identity + spurious)" in text and "| false positives | 2 |" in text


def test_templates_and_empty_ground_truth(tmp_path):
    ev.write_templates(str(tmp_path))
    with pytest.raises(SystemExit, match="no rows"):
        ev.main(["--ground-truth", str(tmp_path / "ground_truth.csv"), "--events-csv", str(tmp_path / "ground_truth.csv")])


def test_manage_users_set_role(api, monkeypatch, capsys):
    import manage_users
    from app import models
    _, factory = api
    db = factory()
    db.add(models.User(email="u@example.com", username="u1", hashed_password="x", role="viewer"))
    db.commit()
    db.close()
    monkeypatch.setattr(manage_users, "SessionLocal", factory)
    monkeypatch.setattr(manage_users, "check_schema_version", lambda: {"state": "unversioned"})
    manage_users.main(["set-role", "u1", "admin"])
    assert "viewer -> admin" in capsys.readouterr().out
    db = factory()
    assert db.query(models.User).filter_by(username="u1").one().role == "admin"
    assert db.query(models.AuditLog).filter_by(username="console").count() == 1
    db.close()
    with pytest.raises(SystemExit):
        manage_users.main(["set-role", "nobody", "admin"])


@pytest.fixture
def cli_db(api, monkeypatch):
    """manage_users wired to the isolated test database, with two accounts"""
    import manage_users
    from app import models
    from app.security import get_password_hash
    _, factory = api
    db = factory()
    db.add(models.User(email="s@example.com", username="Surya", hashed_password=get_password_hash("old-password-1"),
                       role="admin", is_active=True))
    db.add(models.User(email="o@example.com", username="other", hashed_password=get_password_hash("other-pass-1"),
                       role="operator", is_active=True))
    db.commit()
    db.close()
    monkeypatch.setattr(manage_users, "SessionLocal", factory)
    monkeypatch.setattr(manage_users, "check_schema_version", lambda: {"state": "up_to_date"})
    monkeypatch.setattr(manage_users.sys.stdin, "isatty", lambda: True, raising=False)
    return manage_users, factory


def _prompts(monkeypatch, manage_users, *answers):
    answers = list(answers)
    monkeypatch.setattr(manage_users.getpass, "getpass", lambda prompt="": answers.pop(0))


def test_set_password_changes_only_that_hash_and_audits_without_secrets(cli_db, monkeypatch, capsys):
    from app import models
    from app.security import verify_password
    manage_users, factory = cli_db
    db = factory()
    other_hash_before = db.query(models.User).filter_by(username="other").one().hashed_password
    db.close()

    _prompts(monkeypatch, manage_users, "brand-new-pass-9", "brand-new-pass-9")
    manage_users.main(["set-password", "Surya"])
    out = capsys.readouterr().out
    assert "Password updated for Surya (role admin, active)" in out and "brand-new-pass-9" not in out

    db = factory()
    surya = db.query(models.User).filter_by(username="Surya").one()
    assert verify_password("brand-new-pass-9", surya.hashed_password)
    assert not verify_password("old-password-1", surya.hashed_password)
    assert surya.role == "admin" and surya.is_active
    assert db.query(models.User).filter_by(username="other").one().hashed_password == other_hash_before
    entry = db.query(models.AuditLog).filter_by(action="user.password_reset").one()
    assert entry.username == "console" and entry.entity_id == str(surya.id)
    assert "brand-new-pass-9" not in (entry.details or "") and "$2" not in (entry.details or "")
    db.close()


@pytest.mark.parametrize("answers,needle", [
    (("short", "short"), "8-72"),
    (("x" * 73, "x" * 73), "8-72"),
    (("good-password-1", "different-pass-1"), "do not match"),
])
def test_set_password_rejects_bad_input_and_changes_nothing(cli_db, monkeypatch, answers, needle):
    from app import models
    manage_users, factory = cli_db
    db = factory()
    before = db.query(models.User).filter_by(username="Surya").one().hashed_password
    db.close()
    _prompts(monkeypatch, manage_users, *answers)
    with pytest.raises(SystemExit, match=needle):
        manage_users.main(["set-password", "Surya"])
    db = factory()
    assert db.query(models.User).filter_by(username="Surya").one().hashed_password == before
    assert db.query(models.AuditLog).filter_by(action="user.password_reset").count() == 0
    db.close()


def test_set_password_refuses_without_interactive_terminal(cli_db, monkeypatch):
    manage_users, _ = cli_db
    monkeypatch.setattr(manage_users.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(manage_users.getpass, "getpass", lambda prompt="": pytest.fail("must not prompt"))
    with pytest.raises(SystemExit, match="interactive terminal"):
        manage_users.main(["set-password", "Surya"])


def test_set_password_unknown_user(cli_db, monkeypatch):
    manage_users, _ = cli_db
    with pytest.raises(SystemExit, match="No user named"):
        manage_users.main(["set-password", "nobody"])


@pytest.mark.parametrize("argv", [["set-role", "Surya", "operator"], ["deactivate", "Surya"]])
def test_cli_refuses_to_remove_the_last_active_admin(cli_db, argv):
    from app import models
    manage_users, factory = cli_db
    db = factory()
    db.query(models.User).filter_by(username="admin").update({"is_active": False})  # fixture admin
    db.commit()
    db.close()
    with pytest.raises(SystemExit, match="last active admin"):
        manage_users.main(argv)
    db = factory()
    surya = db.query(models.User).filter_by(username="Surya").one()
    assert (surya.role, surya.is_active) == ("admin", True)
    assert db.query(models.AuditLog).count() == 0
    db.close()


def test_cli_allows_demoting_an_admin_while_another_remains(cli_db, capsys):
    manage_users, _ = cli_db
    manage_users.main(["set-role", "Surya", "operator"])  # fixture "admin" stays active
    assert "admin -> operator" in capsys.readouterr().out


@pytest.mark.parametrize("username,email,needle", [
    ("surya", "new@example.com", "already exists"), ("newname", "S@EXAMPLE.com", "already exists"),
    ("ab", "new@example.com", "Invalid account details"), ("newname", "not-an-email", "Invalid account details"),
])
def test_cli_create_rejects_invalid_or_case_insensitive_duplicates(cli_db, monkeypatch, username, email, needle):
    from app import models
    manage_users, factory = cli_db
    monkeypatch.setattr(manage_users.getpass, "getpass", lambda prompt="": pytest.fail("must not prompt"))
    with pytest.raises(SystemExit, match=needle):
        manage_users.main(["create", username, email])
    db = factory()
    assert db.query(models.User).count() == 3
    db.close()


def test_cli_creates_a_linked_student_account_and_refuses_staff_roles_for_it(cli_db, monkeypatch, capsys):
    from app import models
    manage_users, factory = cli_db
    db = factory()
    db.add(models.Student(student_id="RA001", name="Asha", class_name="CSE-A", is_active=True))
    db.commit()
    db.close()
    _prompts(monkeypatch, manage_users, "student-pass-1", "student-pass-1")
    manage_users.main(["create", "asha_s", "asha@college.edu", "--student", "RA001"])
    assert "Created asha_s (student, student RA001)" in capsys.readouterr().out
    db = factory()
    account = db.query(models.User).filter_by(username="asha_s").one()
    assert account.role == "student" and account.student_db_id is not None
    entry = db.query(models.AuditLog).filter_by(action="user.create").one()
    assert '"student_id": "RA001"' in entry.details and "student-pass-1" not in entry.details
    db.close()
    with pytest.raises(SystemExit, match="student account"):
        manage_users.main(["set-role", "asha_s", "operator"])
    monkeypatch.setattr(manage_users.getpass, "getpass", lambda prompt="": pytest.fail("must not prompt"))
    with pytest.raises(SystemExit, match="already has an account"):
        manage_users.main(["create", "asha_two", "two@college.edu", "--student", "RA001"])
    with pytest.raises(SystemExit, match="No current student"):
        manage_users.main(["create", "ghost", "ghost@college.edu", "--student", "NOPE"])
    with pytest.raises(SystemExit, match="leave out --role"):
        manage_users.main(["create", "mixed", "mixed@college.edu", "--student", "RA001", "--role", "admin"])


def test_cli_creates_a_professor_account_that_can_use_the_staff_portal(cli_db, monkeypatch, capsys):
    from app import models
    from app.security import verify_password
    manage_users, factory = cli_db
    _prompts(monkeypatch, manage_users, "prof-pass-123", "prof-pass-123")
    manage_users.main(["create", "prof_rao", "rao@college.edu", "--role", "professor"])
    out = capsys.readouterr().out
    assert "Created prof_rao (professor)" in out and "prof-pass-123" not in out
    db = factory()
    user = db.query(models.User).filter_by(username="prof_rao").one()
    assert user.role == "professor" and user.is_active and user.student_db_id is None
    assert verify_password("prof-pass-123", user.hashed_password)
    entry = db.query(models.AuditLog).filter_by(action="user.create").one()
    assert entry.username == "console" and "prof-pass-123" not in (entry.details or "")
    db.close()
    from app.main import app
    from app.security import create_access_token
    from helpers import ASGIClient
    client = ASGIClient(app, headers={"Authorization": f"Bearer {create_access_token({'sub': 'prof_rao'})}"})
    assert client.get("/staff/timetable").status_code == 200
    assert client.get("/users/").status_code == 403


def test_cli_set_role_to_and_from_professor(cli_db, capsys):
    from app import models
    manage_users, factory = cli_db
    manage_users.main(["set-role", "other", "professor"])
    assert "operator -> professor" in capsys.readouterr().out
    manage_users.main(["set-role", "other", "operator"])
    db = factory()
    assert db.query(models.User).filter_by(username="other").one().role == "operator"
    db.close()


def test_cli_professor_cannot_also_be_a_student_account(cli_db, monkeypatch):
    manage_users, _ = cli_db
    monkeypatch.setattr(manage_users.getpass, "getpass", lambda prompt="": pytest.fail("must not prompt"))
    with pytest.raises(SystemExit, match="leave out --role"):
        manage_users.main(["create", "prof_x", "px@college.edu", "--role", "professor", "--student", "S1"])


def test_no_public_password_reset_endpoint():
    from app.main import app
    paths = {route.path for route in app.routes}
    assert not any("password" in p or "reset" in p for p in paths)
