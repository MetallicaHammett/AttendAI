"""
The b4d2f6a8c1e3 migration (student accounts) only adds users.student_db_id and keeps
every account exactly as it was. Runs Alembic against temporary SQLite files only.
"""
import io
import sqlite3

import pytest
from alembic import command

from test_migrations import alembic_config, columns

PREVIOUS = "e7b3c1a9d2f0"
THIS = "b4d2f6a8c1e3"


def seed(db_path):
    command.upgrade(alembic_config(db_path), PREVIOUS)
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO users (email, username, hashed_password, is_active, role) VALUES ('a@x.org', 'alice', 'h1', 1, 'admin')")
    con.execute("INSERT INTO users (email, username, hashed_password, is_active, role) VALUES ('b@x.org', 'bob', 'h2', 0, 'operator')")
    con.execute("INSERT INTO students (student_id, name, class_name, is_active, person_type) VALUES ('S1', 'Asha', 'CSE-A', 1, 'student')")
    con.execute("INSERT INTO audit_log (action, entity_type) VALUES ('user.update', 'user')")
    con.commit()
    con.close()


ACCOUNT_COLUMNS = "id, email, username, hashed_password, is_active, role, created_at"


def test_upgrade_adds_an_empty_link_and_changes_no_account(tmp_path):
    db_path = tmp_path / "b4.db"
    seed(db_path)
    con = sqlite3.connect(db_path)
    before = con.execute(f"SELECT {ACCOUNT_COLUMNS} FROM users ORDER BY id").fetchall()
    con.close()
    command.upgrade(alembic_config(db_path), THIS)
    con = sqlite3.connect(db_path)
    assert con.execute(f"SELECT {ACCOUNT_COLUMNS} FROM users ORDER BY id").fetchall() == before
    assert con.execute("SELECT count(*) FROM users WHERE student_db_id IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 1
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == THIS
    con.close()


def test_link_must_point_to_a_real_student_and_only_once(tmp_path):
    db_path = tmp_path / "b4.db"
    seed(db_path)
    command.upgrade(alembic_config(db_path), THIS)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE users SET student_db_id = 999 WHERE id = 1")
    con.execute("UPDATE users SET student_db_id = 1 WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE users SET student_db_id = 1 WHERE id = 2")  # one account per student
    con.close()


def test_downgrade_without_links_restores_previous_schema(tmp_path):
    db_path = tmp_path / "b4.db"
    seed(db_path)
    command.upgrade(alembic_config(db_path), THIS)
    command.downgrade(alembic_config(db_path), PREVIOUS)
    con = sqlite3.connect(db_path)
    assert "student_db_id" not in columns(con, "users")
    assert con.execute("SELECT count(*) FROM users").fetchone()[0] == 2
    con.close()


def test_downgrade_refuses_to_drop_student_links_unless_forced(tmp_path):
    db_path = tmp_path / "b4.db"
    seed(db_path)
    command.upgrade(alembic_config(db_path), THIS)
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO users (email, username, hashed_password, is_active, role, student_db_id) "
                "VALUES ('s@x.org', 'asha', 'h3', 1, 'student', 1)")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="1 student account"):
        command.downgrade(alembic_config(db_path), PREVIOUS)
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == THIS
    con.close()
    command.downgrade(alembic_config(db_path, ["force_data_loss=true"]), PREVIOUS)


def test_offline_downgrade_refused(tmp_path):
    cfg = alembic_config(tmp_path / "never-created.db")
    cfg.output_buffer = io.StringIO()
    with pytest.raises(RuntimeError, match="Offline"):
        command.downgrade(cfg, f"{THIS}:{PREVIOUS}", sql=True)
