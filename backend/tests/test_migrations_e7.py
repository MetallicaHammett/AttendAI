"""
The e7b3c1a9d2f0 migration (roles, staff, classrooms, timetable, overrides, audit log,
camera runs) is additive and keeps every existing row.
Runs Alembic against temporary SQLite files only - never data/attendai.db.
"""
import io
import sqlite3

import pytest
from alembic import command

from test_migrations import alembic_config, columns

PREVIOUS = "d5a1e7c2b9f4"
THIS = "e7b3c1a9d2f0"  # later migrations are tested in their own files
NEW_TABLES = {"classrooms", "timetable_periods", "holidays", "attendance_overrides", "audit_log", "camera_runs"}


def tables(con):
    return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def seed_previous_schema(db_path):
    """A database at the previous head with the kinds of rows the real one has"""
    command.upgrade(alembic_config(db_path), PREVIOUS)
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO users (email, username, hashed_password, is_active) VALUES ('a@x.org', 'alice', 'h', 1)")
    con.execute("INSERT INTO users (email, username, hashed_password, is_active) VALUES ('b@x.org', 'bob', 'h', 0)")
    con.execute("INSERT INTO students (student_id, name, class_name, is_active) VALUES ('S1', 'Asha', 'CSE-A', 1)")
    con.execute("INSERT INTO students (student_id, name, is_active, deleted_at) VALUES ('S2', 'Ravi', 0, '2026-10-01 10:00:00')")
    con.execute("INSERT INTO face_embeddings (student_id, embedding_data, is_active) VALUES (1, x'00', 1)")
    con.execute("INSERT INTO cameras (name, source, source_type, is_active, width, height, fps, camera_type) "
                "VALUES ('Door', '0', 'webcam', 1, 640, 480, 30, 'IN')")
    con.execute("INSERT INTO attendance_events (student_id, camera_id, event_type, confidence, timestamp) "
                "VALUES (1, 1, 'ENTRY', 90, '2026-10-19 03:30:00')")
    con.commit()
    con.close()


def test_upgrade_keeps_existing_rows_and_sets_safe_defaults(tmp_path):
    db_path = tmp_path / "e7.db"
    seed_previous_schema(db_path)
    command.upgrade(alembic_config(db_path), THIS)

    con = sqlite3.connect(db_path)
    assert NEW_TABLES <= tables(con)
    # Existing accounts keep day-to-day access but nobody becomes admin automatically
    assert con.execute("SELECT username, role, is_active FROM users ORDER BY id").fetchall() == [
        ("alice", "operator", 1), ("bob", "operator", 0)]
    assert con.execute("SELECT student_id, name, class_name, person_type, deleted_at FROM students ORDER BY id").fetchall() == [
        ("S1", "Asha", "CSE-A", "student", None), ("S2", "Ravi", None, "student", "2026-10-01 10:00:00")]
    assert con.execute("SELECT count(*) FROM face_embeddings").fetchone()[0] == 1
    assert con.execute("SELECT name, source, classroom_id FROM cameras").fetchall() == [("Door", "0", None)]
    assert con.execute("SELECT student_id, camera_id, event_type, confidence, timestamp, source, note "
                       "FROM attendance_events").fetchall() == [
        (1, 1, "ENTRY", 90, "2026-10-19 03:30:00", "camera", None)]
    # Indexes that existed before the batch table rebuilds are still there
    assert "ix_attendance_events_timestamp" in {row[1] for row in con.execute("PRAGMA index_list(attendance_events)")}
    assert "ix_cameras_classroom_id" in {row[1] for row in con.execute("PRAGMA index_list(cameras)")}
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "e7b3c1a9d2f0"
    con.close()


def test_new_registrations_default_to_viewer(tmp_path):
    db_path = tmp_path / "e7.db"
    command.upgrade(alembic_config(db_path), THIS)
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO users (email, username, hashed_password, is_active) VALUES ('c@x.org', 'carol', 'h', 1)")
    assert con.execute("SELECT role FROM users").fetchone()[0] == "viewer"
    con.close()


def test_foreign_keys_still_enforced_after_table_rebuild(tmp_path):
    db_path = tmp_path / "e7.db"
    seed_previous_schema(db_path)
    command.upgrade(alembic_config(db_path), THIS)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO attendance_events (student_id, camera_id, event_type, confidence) VALUES (999, 1, 'ENTRY', 90)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE cameras SET classroom_id = 42")
    con.close()


def test_downgrade_without_new_data_restores_previous_schema(tmp_path):
    db_path = tmp_path / "e7.db"
    seed_previous_schema(db_path)
    cfg = alembic_config(db_path)
    command.upgrade(cfg, THIS)
    con = sqlite3.connect(db_path)
    con.execute("UPDATE users SET role = 'operator'")  # the post-upgrade state, nothing new added
    con.commit()
    con.close()

    command.downgrade(cfg, PREVIOUS)
    con = sqlite3.connect(db_path)
    assert not (NEW_TABLES & tables(con))
    assert "role" not in columns(con, "users") and "person_type" not in columns(con, "students")
    assert "source" not in columns(con, "attendance_events") and "classroom_id" not in columns(con, "cameras")
    assert con.execute("SELECT count(*) FROM attendance_events").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM users").fetchone()[0] == 2
    con.close()


@pytest.mark.parametrize("risky_sql,needle", [
    ("INSERT INTO classrooms (name, is_active) VALUES ('Room 101', 1)", "classrooms rows"),
    ("UPDATE students SET person_type = 'staff' WHERE id = 1", "staff records"),
    ("UPDATE users SET role = 'admin' WHERE id = 1", "admin/viewer roles"),
    ("INSERT INTO audit_log (action, entity_type) VALUES ('test', 'x')", "audit_log rows"),
])
def test_downgrade_refuses_to_lose_new_data_unless_forced(tmp_path, risky_sql, needle):
    db_path = tmp_path / "e7.db"
    seed_previous_schema(db_path)
    command.upgrade(alembic_config(db_path), THIS)
    con = sqlite3.connect(db_path)
    con.execute(risky_sql)
    con.commit()
    con.close()

    with pytest.raises(RuntimeError, match=needle):
        command.downgrade(alembic_config(db_path), PREVIOUS)
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "e7b3c1a9d2f0"
    assert NEW_TABLES <= tables(con)
    con.close()

    command.downgrade(alembic_config(db_path, ["force_data_loss=true"]), PREVIOUS)
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == PREVIOUS
    assert con.execute("SELECT count(*) FROM students").fetchone()[0] == 2
    con.close()


def test_offline_downgrade_refused(tmp_path):
    cfg = alembic_config(tmp_path / "never-created.db")
    cfg.output_buffer = io.StringIO()
    with pytest.raises(RuntimeError, match="Offline"):
        command.downgrade(cfg, f"e7b3c1a9d2f0:{PREVIOUS}", sql=True)
    assert "DROP TABLE" not in cfg.output_buffer.getvalue()


def test_head_schema_matches_models_for_this_migration(tmp_path):
    """No autogenerate difference between Alembic head and the models for anything this
    migration adds. (Older drift is left alone and documented: cameras.width/height/fps
    are nullable in the database, and users.email/username and students.student_id are
    unique constraints rather than unique indexes - equivalent for SQLite.)"""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine
    from app.database import Base
    import app.models  # noqa: F401

    db_path = tmp_path / "head.db"
    command.upgrade(alembic_config(db_path), "head")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    new_names = NEW_TABLES | {"role", "person_type", "classroom_id", "source", "created_by_user_id", "note"}
    relevant = [d for d in diff if any(f"'{name}'" in repr(d) for name in new_names)]
    assert relevant == []
    # The pre-existing drift is exactly the known set (nothing new crept in)
    assert len(diff) == 6
