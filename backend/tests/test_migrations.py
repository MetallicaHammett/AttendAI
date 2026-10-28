"""
The d5a1e7c2b9f4 migration is additive and preserves existing data.
Runs Alembic against temporary SQLite files only - never data/attendai.db.
"""
import argparse
import io
import os
import sqlite3

import pytest
from alembic import command
from alembic.config import Config

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def alembic_config(db_path, x_args=None):
    # no ini file -> env.py skips logging fileConfig; x_args mimic `alembic -x key=value`
    cfg = Config(cmd_opts=argparse.Namespace(x=list(x_args or [])))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def test_upgrade_preserves_existing_rows_and_downgrade_works(tmp_path):
    db_path = tmp_path / "migrate.db"
    cfg = alembic_config(db_path)
    command.upgrade(cfg, "c375dca0cd24")  # schema of the current data/attendai.db

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO students (student_id, name, is_active) VALUES ('S1', 'Alice', 1)")
    con.execute("INSERT INTO cameras (name, source, source_type, is_active, width, height, fps, camera_type) "
                "VALUES ('Door', '0', 'webcam', 1, 640, 480, 30, 'IN')")
    con.execute("INSERT INTO attendance_events (student_id, camera_id, event_type, confidence) VALUES (1, 1, 'ENTRY', 90)")
    con.commit()
    con.close()

    # This test covers the d5a1e7c2b9f4 step (head when it was written); later revisions
    # have their own tests in test_migrations_e7.py
    command.upgrade(cfg, "d5a1e7c2b9f4")
    con = sqlite3.connect(db_path)
    assert "deleted_at" in columns(con, "students") and "deleted_at" in columns(con, "cameras")
    assert {"id", "student_id", "course_code", "semester", "is_active"} <= columns(con, "enrollments")
    assert con.execute("SELECT student_id, name, is_active, deleted_at FROM students").fetchall() == [("S1", "Alice", 1, None)]
    assert con.execute("SELECT name, deleted_at FROM cameras").fetchall() == [("Door", None)]
    assert con.execute("SELECT count(*) FROM attendance_events").fetchone()[0] == 1
    indexes = {row[1] for row in con.execute("PRAGMA index_list(attendance_events)")}
    assert "ix_attendance_events_timestamp" in indexes
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "d5a1e7c2b9f4"
    con.close()

    command.downgrade(cfg, "c375dca0cd24")
    con = sqlite3.connect(db_path)
    assert "deleted_at" not in columns(con, "students")
    assert con.execute("SELECT name FROM sqlite_master WHERE name='enrollments'").fetchone() is None
    assert con.execute("SELECT count(*) FROM attendance_events").fetchone()[0] == 1
    assert con.execute("SELECT student_id, name FROM students").fetchall() == [("S1", "Alice")]
    con.close()


@pytest.mark.parametrize("risky_sql,needle", [
    ("INSERT INTO enrollments (student_id, course_code, course_name, semester, is_active) VALUES (1, 'CS', 'CS', 'F26', 1)",
     "enrollments rows"),
    ("UPDATE students SET deleted_at = '2026-10-19 10:00:00', is_active = 0", "soft-deleted students"),
    ("UPDATE cameras SET deleted_at = '2026-10-19 10:00:00', is_active = 0", "soft-deleted cameras"),
])
def test_downgrade_refuses_to_lose_data_unless_forced(tmp_path, risky_sql, needle):
    db_path = tmp_path / "guard.db"
    command.upgrade(alembic_config(db_path), "d5a1e7c2b9f4")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO students (student_id, name, is_active) VALUES ('S1', 'Alice', 1)")
    con.execute("INSERT INTO cameras (name, source, source_type, is_active, width, height, fps, camera_type) "
                "VALUES ('Door', '0', 'webcam', 1, 640, 480, 30, 'IN')")
    con.execute(risky_sql)
    con.commit()
    con.close()

    with pytest.raises(RuntimeError, match=needle) as exc:
        command.downgrade(alembic_config(db_path), "c375dca0cd24")
    assert "force_data_loss=true" in str(exc.value)

    con = sqlite3.connect(db_path)  # nothing was dropped
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "d5a1e7c2b9f4"
    assert "deleted_at" in columns(con, "students")
    assert con.execute("SELECT name FROM sqlite_master WHERE name='enrollments'").fetchone() is not None
    con.close()

    command.downgrade(alembic_config(db_path, ["force_data_loss=true"]), "c375dca0cd24")
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "c375dca0cd24"
    assert con.execute("SELECT student_id FROM students").fetchall() == [("S1",)]
    con.close()


@pytest.mark.parametrize("x_args", [[], ["force_data_loss=true"]])
def test_offline_sql_downgrade_is_refused_before_emitting_sql(tmp_path, x_args):
    """--sql mode cannot check for data or reflect tables; it must not emit a partial script.
    Offline mode never connects, so the database file is not even created."""
    db_path = tmp_path / "never-created.db"
    cfg = alembic_config(db_path, x_args)
    cfg.output_buffer = io.StringIO()
    with pytest.raises(RuntimeError, match="Offline"):
        command.downgrade(cfg, "d5a1e7c2b9f4:c375dca0cd24", sql=True)
    emitted = cfg.output_buffer.getvalue()
    assert "DROP TABLE enrollments" not in emitted and "DROP INDEX" not in emitted
    assert not db_path.exists()


def test_offline_sql_upgrade_still_supported(tmp_path):
    cfg = alembic_config(tmp_path / "never-created.db")
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, "c375dca0cd24:d5a1e7c2b9f4", sql=True)
    assert "CREATE TABLE enrollments" in cfg.output_buffer.getvalue()


def _db_with_enrollment(tmp_path):
    db_path = tmp_path / "flags.db"
    command.upgrade(alembic_config(db_path), "d5a1e7c2b9f4")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO students (student_id, name, is_active) VALUES ('S1', 'Alice', 1)")
    con.execute("INSERT INTO enrollments (student_id, course_code, course_name, semester, is_active) "
                "VALUES (1, 'CS', 'CS', 'F26', 1)")
    con.commit()
    con.close()
    return db_path


def _version(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("x_arg", ["force_data_loss=false", "force_data_loss=", "force_data_loss=on", "force=true"])
def test_force_flag_requires_explicit_true(tmp_path, x_arg):
    db_path = _db_with_enrollment(tmp_path)
    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        command.downgrade(alembic_config(db_path, [x_arg]), "c375dca0cd24")
    assert _version(db_path) == "d5a1e7c2b9f4"


def test_force_flag_without_value_fails_closed(tmp_path):
    db_path = _db_with_enrollment(tmp_path)
    with pytest.raises(ValueError):  # Alembic cannot parse "-x force_data_loss" as key=value
        command.downgrade(alembic_config(db_path, ["force_data_loss"]), "c375dca0cd24")
    assert _version(db_path) == "d5a1e7c2b9f4"


@pytest.mark.parametrize("value", ["TRUE", "1", "yes"])
def test_force_flag_accepted_values(tmp_path, value):
    db_path = _db_with_enrollment(tmp_path)
    command.downgrade(alembic_config(db_path, [f"force_data_loss={value}"]), "c375dca0cd24")
    assert _version(db_path) == "c375dca0cd24"


def test_migrated_schema_matches_models(tmp_path):
    """Alembic head and the SQLAlchemy models agree (no pending autogenerate diff
    for the tables/columns this phase touches)."""
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

    relevant = [d for d in diff if any(
        name in repr(d) for name in ("deleted_at", "enrollments", "ix_attendance_events_timestamp"))]
    assert relevant == []
