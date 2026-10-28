"""
The f2c8a4e6b1d9 migration (professor portal) only adds timetable_periods.professor_user_id
and the attendance_registers table; every existing row stays as it was. Runs Alembic
against temporary SQLite files only.
"""
import io
import sqlite3

import pytest
from alembic import command

from test_migrations import alembic_config, columns

PREVIOUS = "b4d2f6a8c1e3"
THIS = "f2c8a4e6b1d9"


def seed(db_path):
    command.upgrade(alembic_config(db_path), PREVIOUS)
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO users (email, username, hashed_password, is_active, role) VALUES ('a@x.org', 'alice', 'h1', 1, 'admin')")
    con.execute("INSERT INTO users (email, username, hashed_password, is_active, role) VALUES ('p@x.org', 'prof', 'h2', 1, 'professor')")
    con.execute("INSERT INTO classrooms (name, is_active) VALUES ('Room 101', 1)")
    con.execute("INSERT INTO students (student_id, name, class_name, is_active, person_type) VALUES ('S1', 'Asha', 'CSE-A', 1, 'student')")
    con.execute("INSERT INTO timetable_periods (classroom_id, class_name, course_name, day_of_week, start_time, end_time, "
                "is_active) VALUES (1, 'CSE-A', 'Maths', 0, '09:00:00', '10:00:00', 1)")
    con.execute("INSERT INTO attendance_overrides (period_id, date, student_id, status, reason) "
                "VALUES (1, '2026-10-05', 1, 'EXCUSED', 'Medical')")
    con.commit()
    con.close()


def snapshot(con):
    return {table: con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("users", "students", "attendance_overrides")}


def test_upgrade_adds_empty_assignments_and_registers_and_changes_nothing(tmp_path):
    db_path = tmp_path / "f2.db"
    seed(db_path)
    con = sqlite3.connect(db_path)
    before = snapshot(con)
    periods_before = con.execute("SELECT id, class_name, course_name, start_time FROM timetable_periods").fetchall()
    con.close()
    command.upgrade(alembic_config(db_path), THIS)
    con = sqlite3.connect(db_path)
    assert snapshot(con) == before
    assert con.execute("SELECT id, class_name, course_name, start_time FROM timetable_periods").fetchall() == periods_before
    assert con.execute("SELECT count(*) FROM timetable_periods WHERE professor_user_id IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM attendance_registers").fetchone()[0] == 0
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == THIS
    con.close()


def test_one_register_per_period_and_date_and_real_references(tmp_path):
    db_path = tmp_path / "f2.db"
    seed(db_path)
    command.upgrade(alembic_config(db_path), THIS)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE timetable_periods SET professor_user_id = 999 WHERE id = 1")
    con.execute("UPDATE timetable_periods SET professor_user_id = 2 WHERE id = 1")
    row = ("INSERT INTO attendance_registers (period_id, date, version, first_submitted_by_user_id, first_submitted_at, "
           "submitted_by_user_id, submitted_at) VALUES (1, '2026-10-05', 1, 2, '2026-10-05 10:05', 2, '2026-10-05 10:05')")
    con.execute(row)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(row)  # a second register for the same period and date
    con.close()


def test_downgrade_without_data_restores_previous_schema(tmp_path):
    db_path = tmp_path / "f2.db"
    seed(db_path)
    command.upgrade(alembic_config(db_path), THIS)
    command.downgrade(alembic_config(db_path), PREVIOUS)
    con = sqlite3.connect(db_path)
    assert "professor_user_id" not in columns(con, "timetable_periods")
    assert con.execute("SELECT count(*) FROM sqlite_master WHERE name = 'attendance_registers'").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM attendance_overrides").fetchone()[0] == 1  # decisions kept
    con.close()


def test_downgrade_refuses_to_drop_assignments_unless_forced(tmp_path):
    db_path = tmp_path / "f2.db"
    seed(db_path)
    command.upgrade(alembic_config(db_path), THIS)
    con = sqlite3.connect(db_path)
    con.execute("UPDATE timetable_periods SET professor_user_id = 2 WHERE id = 1")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="1 professor assignment"):
        command.downgrade(alembic_config(db_path), PREVIOUS)
    command.downgrade(alembic_config(db_path, ["force_data_loss=true"]), PREVIOUS)


def test_offline_downgrade_refused(tmp_path):
    cfg = alembic_config(tmp_path / "never-created.db")
    cfg.output_buffer = io.StringIO()
    with pytest.raises(RuntimeError, match="Offline"):
        command.downgrade(cfg, f"{THIS}:{PREVIOUS}", sql=True)
