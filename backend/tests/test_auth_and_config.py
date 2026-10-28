"""
Inactive-user authentication checks and SECRET_KEY configuration.
"""
import pytest

from app import models
from app.config import INSECURE_SECRET_KEYS, Settings
from app.security import create_access_token
from helpers import ASGIClient

PASSWORD = "correct-horse-battery"


@pytest.fixture
def auth_client(api, registration_open):
    """Unauthenticated client + factory, with two users created through self-registration"""
    client, factory = api
    anon = ASGIClient(client.app)
    for name in ("alice", "bob"):
        r = anon.post("/auth/register", json={"email": f"{name}@example.com", "username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
    return anon, factory


def deactivate(factory, username):
    db = factory()
    user = db.query(models.User).filter(models.User.username == username).one()
    user.is_active = False
    db.commit()
    db.close()


def login(client, identifier, password=PASSWORD):
    return client.post("/auth/login", data={"username": identifier, "password": password})


@pytest.mark.parametrize("identifier", ["alice", "alice@example.com"])
def test_active_user_can_log_in_by_username_or_email(auth_client, identifier):
    client, _ = auth_client
    r = login(client, identifier)
    assert r.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["username"] == "alice"


@pytest.mark.parametrize("identifier", ["alice", "alice@example.com"])
def test_inactive_user_cannot_log_in_by_username_or_email(auth_client, identifier):
    client, factory = auth_client
    deactivate(factory, "alice")
    r = login(client, identifier)
    assert r.status_code == 400 and r.json()["detail"] == "Inactive user"


def test_wrong_password_rejected(auth_client):
    client, _ = auth_client
    assert login(client, "alice", "wrong-password").status_code == 401


@pytest.mark.parametrize("subject", ["bob", "bob@example.com"])
def test_existing_token_rejected_after_deactivation(auth_client, subject):
    """Regression: a token whose subject resolved by *username* skipped the is_active check"""
    client, factory = auth_client
    headers = {"Authorization": f"Bearer {create_access_token({'sub': subject})}"}
    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.get("/students/", headers=headers).status_code == 200
    deactivate(factory, "bob")
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/students/", headers=headers).status_code == 401


def test_token_for_unknown_user_rejected(auth_client):
    client, _ = auth_client
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'ghost'})}"}
    assert client.get("/auth/me", headers=headers).status_code == 401


# --- SECRET_KEY ---------------------------------------------------------------

STRONG_KEY = "x" * 64


@pytest.fixture(autouse=True)
def no_debug_env(monkeypatch):
    """conftest exports DEBUG=True for the app; Settings() under test must not inherit it"""
    monkeypatch.delenv("DEBUG", raising=False)


@pytest.mark.parametrize("key", sorted(INSECURE_SECRET_KEYS) + ["short-key"])
def test_production_refuses_missing_placeholder_or_short_secret(key):
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY=key)


@pytest.mark.parametrize("environment", ["production", "development"])
def test_missing_setting_never_opens_self_registration(monkeypatch, environment):
    """A deployment whose .env lacks ALLOW_SELF_REGISTRATION keeps public sign-up closed"""
    monkeypatch.delenv("ALLOW_SELF_REGISTRATION", raising=False)
    assert Settings(_env_file=None, ENVIRONMENT=environment, SECRET_KEY=STRONG_KEY).ALLOW_SELF_REGISTRATION is False


@pytest.mark.parametrize("environment", ["production", "development"])
def test_missing_host_setting_binds_to_loopback(monkeypatch, environment):
    """`python -m app.main` without HOST must not listen on the whole network"""
    monkeypatch.delenv("HOST", raising=False)
    s = Settings(_env_file=None, ENVIRONMENT=environment, SECRET_KEY=STRONG_KEY)
    assert s.HOST == "127.0.0.1"


def test_env_example_binds_to_loopback():
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, ".env.example"), encoding="utf-8") as f:
        values = dict(line.strip().split("=", 1) for line in f if "=" in line and not line.lstrip().startswith("#"))
    assert values.get("HOST") == "127.0.0.1" and "0.0.0.0" not in values.values()


def test_production_accepts_strong_secret_and_defaults_debug_off():
    s = Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY=STRONG_KEY)
    assert s.SECRET_KEY.get_secret_value() == STRONG_KEY and s.DEBUG is False and s.is_production


def test_production_refuses_debug_and_synthetic_camera():
    with pytest.raises(ValueError, match="DEBUG"):
        Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY=STRONG_KEY, DEBUG=True)
    with pytest.raises(ValueError, match="ENABLE_SYNTHETIC_CAMERA"):
        Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY=STRONG_KEY, ENABLE_SYNTHETIC_CAMERA=True)


@pytest.mark.parametrize("environment", ["", "prod", "staging"])
def test_environment_is_required_and_validated(environment, monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(ValueError, match="ENVIRONMENT"):
        Settings(_env_file=None, ENVIRONMENT=environment, SECRET_KEY=STRONG_KEY)


def test_missing_environment_variable_fails_startup(monkeypatch):
    """Regression: DEBUG used to default to True, so an unconfigured deployment ran with dev rules"""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    with pytest.raises(ValueError, match="ENVIRONMENT"):
        Settings(_env_file=None, SECRET_KEY=STRONG_KEY)


def test_environment_is_normalised():
    assert Settings(_env_file=None, ENVIRONMENT=" Production ", SECRET_KEY=STRONG_KEY).ENVIRONMENT == "production"


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_replaces_placeholder_with_random_key(environment):
    a = Settings(_env_file=None, ENVIRONMENT=environment, SECRET_KEY="your-secret-key-here-change-in-production")
    b = Settings(_env_file=None, ENVIRONMENT=environment, SECRET_KEY="")
    a_key, b_key = a.SECRET_KEY.get_secret_value(), b.SECRET_KEY.get_secret_value()
    assert a_key not in INSECURE_SECRET_KEYS and len(a_key) >= 32
    assert a_key != b_key


def test_configured_key_is_used_unchanged_in_development():
    s = Settings(_env_file=None, ENVIRONMENT="development", SECRET_KEY=STRONG_KEY)
    assert s.SECRET_KEY.get_secret_value() == STRONG_KEY


SENTINEL_KEY = "SENTINEL-do-not-leak-" + "q" * 40


def test_secret_key_never_appears_in_repr_str_or_dumps():
    s = Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY=SENTINEL_KEY)
    for rendered in (repr(s), str(s), repr(s.SECRET_KEY), str(s.SECRET_KEY),
                     s.model_dump_json(), str(s.model_dump())):
        assert "SENTINEL" not in rendered
    assert s.SECRET_KEY.get_secret_value() == SENTINEL_KEY


def test_secret_key_not_in_validation_errors_or_warnings(caplog):
    weak = "SENTINEL-short"
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None, ENVIRONMENT="production", SECRET_KEY=weak)
    assert "SENTINEL" not in str(exc.value)
    with caplog.at_level("WARNING"):
        Settings(_env_file=None, ENVIRONMENT="development", SECRET_KEY=weak)
    assert "SENTINEL" not in caplog.text and "too short" in caplog.text


def test_tokens_signed_and_verified_with_secret_value(auth_client):
    """SecretStr must be unwrapped for JWT signing: a real login round-trips"""
    client, _ = auth_client
    token = login(client, "alice").json()["access_token"]
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_health_reports_configured_environment(api):
    client, _ = api
    assert client.get("/health").json()["environment"] == "test"
