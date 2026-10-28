"""
Test helpers shared by the API tests.
"""
import asyncio
from datetime import date, datetime, time, timezone

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base, enable_sqlite_foreign_keys


class ASGIClient:
    """Minimal sync client over httpx.ASGITransport. Used instead of
    fastapi.testclient.TestClient, which breaks with the installed
    starlette 0.27 + httpx >= 0.28 combination in some local environments."""

    def __init__(self, app, headers=None):
        self.app = app
        self.headers = dict(headers or {})

    def request(self, method, url, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}

        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                return await http.request(method, url, headers=headers, **kwargs)
        return asyncio.run(send())

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)


def make_session_factory(db_path):
    # Same foreign-key enforcement as the app engine
    engine = enable_sqlite_foreign_keys(
        create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}))
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def utc_naive(day: date, at: time) -> datetime:
    """Local wall-clock (day, at) as naive UTC - the format the DB stores"""
    return datetime.combine(day, at).astimezone().astimezone(timezone.utc).replace(tzinfo=None)


def add_student(db, sid, active=True, name=None):
    student = models.Student(student_id=sid, name=name or f"Student {sid}", is_active=active)
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def add_camera(db, name="Door", camera_type="IN"):
    camera = models.Camera(name=name, source="0", source_type="webcam", camera_type=camera_type)
    db.add(camera)
    db.commit()
    db.refresh(camera)
    return camera


def add_event(db, student_id, event_type="ENTRY", ts=None, camera_id=None, confidence=90):
    event = models.AttendanceEvent(student_id=student_id, camera_id=camera_id, event_type=event_type,
                                   confidence=confidence)
    if ts is not None:
        event.timestamp = ts
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
