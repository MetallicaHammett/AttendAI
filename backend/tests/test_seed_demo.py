"""
scripts/seed_demo.py builds a separate, clearly labelled demo database and can never
target the real one. Runs against temporary files only.
"""
import os
import sqlite3
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BACKEND_DIR, "scripts"))
import seed_demo  # noqa: E402


@pytest.mark.parametrize("target,needle", [
    (os.path.join(BACKEND_DIR, "data", "attendai.db"), "backend/data"),
    (os.path.join(BACKEND_DIR, "data", "attendai_demo.db"), "backend/data"),
    ("elsewhere/attendai.db", "must contain 'demo'"),
])
def test_refuses_the_real_database_and_unlabelled_files(tmp_path, target, needle):
    with pytest.raises(SystemExit, match=needle):
        seed_demo.main(["--db", target, "--credentials", str(tmp_path / "c.txt")])
    assert not os.path.exists(tmp_path / "c.txt")


def test_builds_labelled_demo_data_without_camera_recognitions(tmp_path):
    db_path, creds = tmp_path / "attendai_demo.db", tmp_path / "demo_credentials.txt"
    seed_demo.main(["--db", str(db_path), "--credentials", str(creds)])
    con = sqlite3.connect(db_path)
    names = [r[0] for r in con.execute("SELECT name FROM students")]
    assert names and all(n.startswith("DEMO") for n in names)
    assert all(r[0].startswith("DEMO") for r in con.execute("SELECT course_name FROM timetable_periods"))
    # Attendance is stored as labelled manual events, never as camera recognitions
    assert con.execute("SELECT count(*) FROM attendance_events").fetchone()[0] > 0
    assert con.execute("SELECT count(*) FROM attendance_events WHERE source != 'manual' OR note NOT LIKE 'DEMO%' "
                       "OR camera_id IS NOT NULL").fetchone()[0] == 0
    assert {r[0] for r in con.execute("SELECT feed_kind FROM camera_runs")} == {"synthetic"}
    assert con.execute("SELECT count(*) FROM cameras WHERE is_active = 1").fetchone()[0] == 0
    users = dict(con.execute("SELECT username, role FROM users"))
    assert users == {"demo_admin": "admin", "demo_operator": "operator", "demo_viewer": "viewer",
                     "demo_student": "student", "demo_student2": "student", "demo_professor": "professor"}
    assert con.execute("SELECT count(*) FROM users WHERE role = 'student' AND student_db_id IS NULL").fetchone()[0] == 0
    con.close()
    text = creds.read_text(encoding="utf-8")
    assert "do not work on the real AttendAI" in text and text.count("Demo-") == 6
    with pytest.raises(SystemExit, match="--replace"):
        seed_demo.main(["--db", str(db_path), "--credentials", str(creds)])


def test_prints_no_passwords(tmp_path, capsys):
    creds = tmp_path / "demo_credentials.txt"
    seed_demo.main(["--db", str(tmp_path / "x_demo.db"), "--credentials", str(creds)])
    out = capsys.readouterr().out
    for line in creds.read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            assert line.split("\t")[1] not in out
