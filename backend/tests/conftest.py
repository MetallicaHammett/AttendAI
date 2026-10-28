"""
Shared pytest setup for the Phase 1 attendance-pipeline tests.

Environment variables are set before any app module is imported so tests never
touch the real data/attendai.db and do not depend on the developer .env file.
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ["SECRET_KEY"] = "test-secret-key-for-pytest-only-0123456789-abcdefghijklmnop"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "True"
os.environ["ENABLE_SYNTHETIC_CAMERA"] = "False"
# Same as the production configuration; tests that exercise sign-up opt in explicitly
# with the `registration_open` fixture below
os.environ["ALLOW_SELF_REGISTRATION"] = "False"
# Most pipeline tests stub dlib/face_recognition with hand-made 128-d float64 encodings.
# The production default is "sface"; tests of that path use the `sface_active` fixture.
os.environ["FACE_RECOGNITION_MODEL"] = "dlib"

import tempfile  # noqa: E402

# app.config creates the shared `settings` object once, on first import, from the
# environment AND from `.env` in the current directory. Import it from an empty directory
# so the developer's backend/.env (production secrets, DEBUG, registration, database path)
# can never leak into the tests. Every later Settings() in the tests passes _env_file=None.
_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as _empty_dir:
    os.chdir(_empty_dir)
    try:
        from app.config import settings as _settings  # noqa: E402
    finally:
        os.chdir(_cwd)
assert _settings.DATABASE_URL == "sqlite:///:memory:", "tests must never use a file database from .env"
assert _settings.ENVIRONMENT == "test" and _settings.DEBUG is True
assert _settings.ALLOW_SELF_REGISTRATION is False

import pytest  # noqa: E402

from app import services  # noqa: E402


@pytest.fixture
def registration_open(monkeypatch):
    """For tests that need public self-registration to succeed"""
    monkeypatch.setattr(_settings, "ALLOW_SELF_REGISTRATION", True)


@pytest.fixture
def sface_active(monkeypatch):
    """Run with the production face model (OpenCV YuNet + SFace)"""
    monkeypatch.setattr(_settings, "FACE_RECOGNITION_MODEL", "sface")


@pytest.fixture
def registration_closed(monkeypatch):
    """For tests that check sign-ups are refused"""
    monkeypatch.setattr(_settings, "ALLOW_SELF_REGISTRATION", False)


@pytest.fixture
def api_db(tmp_path):
    """(engine, SessionFactory) for an isolated SQLite file created from the models"""
    from helpers import make_session_factory
    engine, factory = make_session_factory(tmp_path / "api.db")
    yield engine, factory
    engine.dispose()


@pytest.fixture
def api(api_db, monkeypatch):
    """The real FastAPI app wired to the isolated DB, authenticated as an active user.
    Yields (client, SessionFactory)."""
    from helpers import ASGIClient
    from app.main import app
    from app.database import get_db
    from app.routers import video
    from app.security import create_access_token
    from app import models

    engine, factory = api_db
    monkeypatch.setattr(video, "SessionLocal", factory)
    monkeypatch.setattr("app.database.engine", engine)

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db

    db = factory()
    # Token-authenticated admin; the password hash is never checked by these tests.
    # Role restrictions are covered by test_permissions.py.
    db.add(models.User(email="admin@example.com", username="admin",
                       hashed_password="unused", is_active=True, role="admin"))
    db.commit()
    db.close()

    token = create_access_token({"sub": "admin"})
    yield ASGIClient(app, headers={"Authorization": f"Bearer {token}"}), factory
    app.dependency_overrides.clear()
    video.active_sessions.clear()


@pytest.fixture(autouse=True)
def fresh_shared_engine():
    """Each test gets a fresh process-wide AttendanceEngine and unknown tracker"""
    services.reset_shared_attendance_engine()
    services.reset_shared_unknown_tracker()
    yield
    services.reset_shared_attendance_engine()
    services.reset_shared_unknown_tracker()
