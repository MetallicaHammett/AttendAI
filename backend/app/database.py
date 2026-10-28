import sqlite3

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker
from urllib.parse import urlparse

from app.config import settings

# Determine connect arguments based on database type
def get_connect_args(database_url):
    """Return appropriate connect arguments based on database URL."""
    parsed = urlparse(database_url)
    if parsed.scheme.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}

def enable_sqlite_foreign_keys(target_engine):
    """SQLite ignores FOREIGN KEY constraints unless PRAGMA foreign_keys=ON is set
    on every connection. With it on, deleting a student/camera that still has
    attendance history fails instead of orphaning or nulling those rows.
    (Not applied to Alembic's engine: batch migrations recreate tables and must
    run with foreign keys off.)"""
    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return target_engine


# Create SQLAlchemy engine
engine = enable_sqlite_foreign_keys(create_engine(
    settings.DATABASE_URL,
    connect_args=get_connect_args(settings.DATABASE_URL)
))

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class for models
Base = declarative_base()

def check_database_connection() -> tuple[bool, str]:
    """Query the configured database. Returns (ok, detail).

    Also reads the users table: SQLite happily "connects" to an empty or newly
    created file, so SELECT 1 alone would report a missing/unmigrated DB as healthy.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text("SELECT 1 FROM users LIMIT 1"))
        return True, "ok"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

def expected_schema_revision() -> str:
    """Alembic head revision shipped with this code (read from the local migration files)"""
    import os
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config()
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def check_schema_version() -> dict:
    """{"state": "up_to_date" | "outdated" | "unversioned" | "error", "current", "expected"}.
    "outdated" means the database needs `alembic upgrade head` (after a backup)."""
    try:
        expected = expected_schema_revision()
    except Exception as e:
        return {"state": "error", "current": None, "expected": None, "detail": f"{type(e).__name__}: {e}"}
    try:
        with engine.connect() as connection:
            exists = connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")).first() \
                if engine.dialect.name == "sqlite" else True
            if not exists:
                return {"state": "unversioned", "current": None, "expected": expected}
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as e:
        return {"state": "error", "current": None, "expected": expected, "detail": f"{type(e).__name__}: {e}"}
    return {"state": "up_to_date" if current == expected else "outdated", "current": current, "expected": expected}


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()