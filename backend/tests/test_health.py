"""
/health performs a real database check and reports failure with 503.
"""
from sqlalchemy import create_engine


def test_health_ok_with_migrated_database(api):
    client, _ = api
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy" and body["checks"]["database"] == "ok"


def test_health_503_when_database_unreachable(api, monkeypatch, tmp_path):
    client, _ = api
    broken = create_engine(f"sqlite:///{tmp_path / 'missing-dir' / 'nope.db'}")
    monkeypatch.setattr("app.database.engine", broken)
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unhealthy" and body["checks"]["database"] == "error"
    assert "OperationalError" in body["errors"]["database"]


def test_health_503_when_schema_missing(api, monkeypatch, tmp_path):
    """An empty SQLite file "connects" fine; the check must still fail"""
    client, _ = api
    monkeypatch.setattr("app.database.engine", create_engine(f"sqlite:///{tmp_path / 'empty.db'}"))
    r = client.get("/health")
    assert r.status_code == 503
    assert "users" in r.json()["errors"]["database"]
