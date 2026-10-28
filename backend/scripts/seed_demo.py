"""
Build a separate DEMO database with clearly labelled fictional data, so every screen can be
seen before real classes, cameras and students are set up.

    .\\.venv\\Scripts\\python.exe scripts\\seed_demo.py --db ..\\demo\\attendai_demo.db --credentials ..\\private\\demo_credentials.txt

Safety:
- Refuses to write anywhere in backend/data (the real database lives there) and refuses any
  file that is not named *demo*.db.
- Never opens a camera. The demo camera is inactive and points at no device.
- Everything is labelled: people, room and courses start with "DEMO", attendance events are
  stored as manual events with the note "DEMO - synthetic, not from a camera" (so the UI shows
  them as manual, with no recognition confidence), camera runs are recorded as feed_kind
  "synthetic". Nothing here is a face-recognition result.
- Demo account passwords are random, written only to the --credentials file (keep it in the
  git-ignored private/ folder), never printed.
"""
import argparse
import os
import secrets
import sys
from datetime import date, datetime, time, timedelta, timezone

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_NOTE = "DEMO - synthetic, not from a camera"
DEMO_PASSWORD_PREFIX = "Demo-"  # marks demo passwords; the rest is random


def check_target(path: str) -> str:
    path = os.path.abspath(path)
    data_dir = os.path.abspath(os.path.join(BACKEND_DIR, "data"))
    if os.path.commonpath([path, data_dir]) == data_dir:
        sys.exit("Refusing: the demo database must not be inside backend/data (the real database lives there)")
    if not (os.path.basename(path).endswith(".db") and "demo" in os.path.basename(path).lower()):
        sys.exit("Refusing: the demo database file name must contain 'demo' and end in .db")
    return path


def utc(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm)).astimezone().astimezone(timezone.utc).replace(tzinfo=None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create the AttendAI DEMO database (fictional, labelled data)")
    parser.add_argument("--db", required=True, help="new demo database file, e.g. ..\\demo\\attendai_demo.db")
    parser.add_argument("--credentials", required=True, help="where to write the demo account passwords")
    parser.add_argument("--replace", action="store_true", help="replace an existing demo database at --db")
    args = parser.parse_args(argv)
    db_path = check_target(args.db)
    if os.path.exists(db_path):
        if not args.replace:
            sys.exit(f"{db_path} exists; pass --replace to rebuild the demo database")
        os.remove(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    url = "sqlite:///" + db_path.replace(os.sep, "/")

    from alembic import command
    from alembic.config import Config
    cfg = Config(cmd_opts=argparse.Namespace(x=[]))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")  # same schema as production, built from the real migrations

    sys.path.insert(0, BACKEND_DIR)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import models
    from app.security import get_password_hash

    engine = create_engine(url)
    db = sessionmaker(bind=engine)()
    today = date.today()
    room = models.Classroom(name="DEMO Room 204", building="DEMO block", is_active=True)
    db.add(room)
    db.flush()
    camera = models.Camera(name="DEMO camera (not connected)", source="demo-not-a-device.mp4", source_type="video",
                           camera_type="IN", classroom_id=room.id, is_active=False,
                           description="Demo record only. It points at no device and is inactive.")
    db.add(camera)
    people = [("DEMO-001", "DEMO Asha Kumar"), ("DEMO-002", "DEMO Ravi Shankar"), ("DEMO-003", "DEMO Meera Iyer"),
              ("DEMO-004", "DEMO Arjun Rao"), ("DEMO-005", "DEMO Priya Nair")]
    students = [models.Student(student_id=sid, name=name, class_name="DEMO CSE-B", department="DEMO CSE", is_active=True)
                for sid, name in people]
    db.add_all(students)
    db.add(models.Student(student_id="DEMO-T01", name="DEMO Staff Member", class_name="DEMO CSE-B", person_type="staff",
                          department="DEMO CSE", is_active=True))
    db.flush()

    start = today - timedelta(days=56)
    plan = [("DEMO201J", "DEMO Data Structures and Algorithms", 0, (9, 0), (10, 0)),
            ("DEMO202J", "DEMO Operating Systems", 0, (10, 15), (11, 15)),
            ("DEMO206T", "DEMO Probability and Queueing Theory", 2, (9, 0), (10, 0)),
            ("DEMO203P", "DEMO Advanced Programming Practice", 4, (11, 0), (12, 0)),
            ("DEMO204T", "DEMO Computer Organization", 3, (14, 0), (15, 0))]
    for code, name, dow, (sh, sm), (eh, em) in plan:
        period = models.TimetablePeriod(classroom_id=room.id, class_name="DEMO CSE-B", course_code=code, course_name=name,
                                        day_of_week=dow, start_time=time(sh, sm), end_time=time(eh, em), grace_minutes=10,
                                        is_active=True)
        period.created_at = utc(start, 8, 0)
        db.add(period)
    holiday = today - timedelta(days=(today.weekday() - 2) % 7 + 14)
    db.add(models.Holiday(date=holiday, name="DEMO holiday"))

    # Arrival pattern per student: (on-time 08:50, late 09:20, absent) chosen per class day
    rng = secrets.SystemRandom()
    profile = {0: (0.92, 0.04), 1: (0.70, 0.15), 2: (0.80, 0.05), 3: (0.55, 0.15), 4: (0.85, 0.10)}
    day = start
    while day < today:
        if day.weekday() in (0, 2, 3, 4) and day != holiday:
            db.add(models.CameraRun(camera_id=camera.id, started_at=utc(day, 8, 30), stopped_at=utc(day, 15, 30),
                                    feed_kind="synthetic"))
            for i, student in enumerate(students):
                on_time, late = profile[i]
                roll = rng.random()
                if roll < on_time:
                    arrive = (8, 50)
                elif roll < on_time + late:
                    arrive = (9, 20)
                else:
                    continue
                db.add(models.AttendanceEvent(student_id=student.id, camera_id=None, event_type="ENTRY",
                                              timestamp=utc(day, *arrive), confidence=100, source="manual", note=DEMO_NOTE))
                db.add(models.AttendanceEvent(student_id=student.id, camera_id=None, event_type="EXIT",
                                              timestamp=utc(day, 15, 10), confidence=100, source="manual", note=DEMO_NOTE))
        day += timedelta(days=1)

    credentials = {}
    accounts = {}
    for username, role, student in (("demo_admin", "admin", None), ("demo_operator", "operator", None),
                                    ("demo_viewer", "viewer", None), ("demo_student", "student", students[0]),
                                    ("demo_student2", "student", students[3]), ("demo_professor", "professor", None)):
        password = DEMO_PASSWORD_PREFIX + secrets.token_urlsafe(12)  # random every run
        credentials[username] = password
        accounts[username] = models.User(username=username, email=f"{username}@demo.invalid", role=role, is_active=True,
                                         student_db_id=student.id if student else None,
                                         hashed_password=get_password_hash(password))
        db.add(accounts[username])
    db.flush()
    # The demo professor teaches every demo class (staff portal)
    db.query(models.TimetablePeriod).update({"professor_user_id": accounts["demo_professor"].id})
    db.commit()
    counts = {t.__tablename__: db.query(t).count() for t in (models.User, models.Student, models.TimetablePeriod,
                                                              models.AttendanceEvent, models.CameraRun)}
    db.close()
    engine.dispose()

    with open(args.credentials, "w", encoding="utf-8") as f:
        f.write("AttendAI DEMO instance - fictional accounts for the separate demo database only.\n"
                "These do not work on the real AttendAI (port 3000/8000).\n\n")
        for username, password in credentials.items():
            f.write(f"{username}\t{password}\n")
    print(f"Demo database ready: {db_path}")
    print("Rows:", counts)
    print(f"Demo account passwords written to {os.path.abspath(args.credentials)} (not shown here)")


if __name__ == "__main__":
    main()
