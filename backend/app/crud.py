"""
CRUD operations for AttendAI models.
"""
from sqlalchemy.orm import Session, contains_eager
from sqlalchemy import String, and_, desc, func, type_coerce
from typing import List, Optional
from datetime import date, datetime, timedelta, timezone, time as dtime

from app import models
from app.schemas import *
from app.security import get_password_hash


# User CRUD operations
def get_user(db: Session, user_id: int) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.id == user_id).first()


def get_user_by_email(db: Session, email: str) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.email == email).first()


def get_user_by_username(db: Session, username: str) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.username == username).first()


def find_conflicting_user(db: Session, username: str, email: str) -> Optional[str]:
    """'email' or 'username' if an existing account already uses it, ignoring letter case
    (so look-alike accounts cannot be created), else None"""
    if db.query(models.User).filter(func.lower(models.User.email) == email.strip().lower()).first():
        return "email"
    if db.query(models.User).filter(func.lower(models.User.username) == username.strip().lower()).first():
        return "username"
    return None


def count_other_active_admins(db: Session, excluding_user_id: int) -> int:
    return db.query(func.count(models.User.id)).filter(
        models.User.role == "admin", models.User.is_active == True,  # noqa: E712
        models.User.id != excluding_user_id).scalar()


def get_users(db: Session, skip: int = 0, limit: int = 100) -> List[models.User]:
    return db.query(models.User).offset(skip).limit(limit).all()


def create_user(db: Session, user: UserCreate) -> models.User:
    # Hash the password before storing
    hashed_password = get_password_hash(user.password)
    user_data = user.model_dump()
    user_data["hashed_password"] = hashed_password
    # Remove the plain password from the data
    del user_data["password"]
    # Never trust a role from the request: new accounts are read-only
    user_data["role"] = "viewer"
    db_user = models.User(**user_data)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


# Student CRUD operations
def get_student(db: Session, student_id: int) -> Optional[models.Student]:
    return db.query(models.Student).filter(models.Student.id == student_id).first()


def get_live_student(db: Session, student_id: int) -> Optional[models.Student]:
    """Student that exists and has not been soft-deleted"""
    return db.query(models.Student).filter(
        models.Student.id == student_id, models.Student.deleted_at.is_(None)
    ).first()


def get_student_by_student_id(db: Session, student_id: str) -> Optional[models.Student]:
    return db.query(models.Student).filter(models.Student.student_id == student_id).first()


def get_students(db: Session, skip: int = 0, limit: int = 100, include_deleted: bool = False) -> List[models.Student]:
    query = db.query(models.Student)
    if not include_deleted:
        query = query.filter(models.Student.deleted_at.is_(None))
    return query.offset(skip).limit(limit).all()


def create_student(db: Session, student: StudentCreate) -> models.Student:
    db_student = models.Student(**student.model_dump())
    db.add(db_student)
    db.commit()
    db.refresh(db_student)
    return db_student


def update_student(db: Session, student_id: int, student: StudentUpdate) -> Optional[models.Student]:
    db_student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if db_student:
        update_data = student.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_student, field, value)
        db.commit()
        db.refresh(db_student)
    return db_student


def delete_student(db: Session, student_id: int) -> bool:
    """Soft delete: keep the row (and its attendance history), mark it deleted and
    inactive, and deactivate its face embeddings so it is no longer recognised."""
    db_student = db.query(models.Student).filter(
        models.Student.id == student_id, models.Student.deleted_at.is_(None)
    ).first()
    if db_student:
        db_student.deleted_at = datetime.now(timezone.utc)
        db_student.is_active = False
        for embedding in db_student.face_embeddings:
            embedding.is_active = False
        db.commit()
        return True
    return False


# Face Embedding CRUD operations
def get_face_embedding(db: Session, embedding_id: int) -> Optional[models.FaceEmbedding]:
    return db.query(models.FaceEmbedding).filter(models.FaceEmbedding.id == embedding_id).first()


def get_face_embeddings_by_student(db: Session, student_id: int) -> List[models.FaceEmbedding]:
    return db.query(models.FaceEmbedding).filter(models.FaceEmbedding.student_id == student_id).all()


def get_face_embeddings(db: Session, active_only: bool = True) -> List[models.FaceEmbedding]:
    """Get all face embeddings, optionally filtering by active status"""
    query = db.query(models.FaceEmbedding)
    if active_only:
        query = query.filter(models.FaceEmbedding.is_active == True)
    return query.all()


def create_face_embedding(db: Session, embedding: FaceEmbeddingCreate, student_id: int) -> models.FaceEmbedding:
    embedding_data = embedding.model_dump()
    embedding_data['student_id'] = student_id
    db_embedding = models.FaceEmbedding(**embedding_data)
    db.add(db_embedding)
    db.commit()
    db.refresh(db_embedding)
    return db_embedding


def update_face_embedding(db: Session, embedding_id: int, embedding: FaceEmbeddingBase) -> Optional[models.FaceEmbedding]:
    db_embedding = db.query(models.FaceEmbedding).filter(models.FaceEmbedding.id == embedding_id).first()
    if db_embedding:
        update_data = embedding.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_embedding, field, value)
        db.commit()
        db.refresh(db_embedding)
    return db_embedding


def delete_face_embedding(db: Session, embedding_id: int) -> bool:
    db_embedding = db.query(models.FaceEmbedding).filter(models.FaceEmbedding.id == embedding_id).first()
    if db_embedding:
        db.delete(db_embedding)
        db.commit()
        return True
    return False


# Camera CRUD operations
def get_camera(db: Session, camera_id: int) -> Optional[models.Camera]:
    return db.query(models.Camera).filter(models.Camera.id == camera_id).first()


def get_live_camera(db: Session, camera_id: int) -> Optional[models.Camera]:
    """Camera that exists and has not been soft-deleted"""
    return db.query(models.Camera).filter(
        models.Camera.id == camera_id, models.Camera.deleted_at.is_(None)
    ).first()


def get_cameras(db: Session, skip: int = 0, limit: int = 100, include_deleted: bool = False) -> List[models.Camera]:
    query = db.query(models.Camera)
    if not include_deleted:
        query = query.filter(models.Camera.deleted_at.is_(None))
    return query.offset(skip).limit(limit).all()


def create_camera(db: Session, camera: CameraCreate) -> models.Camera:
    db_camera = models.Camera(**camera.model_dump())
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    return db_camera


def update_camera(db: Session, camera_id: int, camera: CameraUpdate) -> Optional[models.Camera]:
    db_camera = db.query(models.Camera).filter(models.Camera.id == camera_id).first()
    if db_camera:
        update_data = camera.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_camera, field, value)
        db.commit()
        db.refresh(db_camera)
    return db_camera


def delete_camera(db: Session, camera_id: int) -> bool:
    """Soft delete: keep the row (and its attendance history), mark it deleted and inactive."""
    db_camera = db.query(models.Camera).filter(
        models.Camera.id == camera_id, models.Camera.deleted_at.is_(None)
    ).first()
    if db_camera:
        db_camera.deleted_at = datetime.now(timezone.utc)
        db_camera.is_active = False
        db.commit()
        return True
    return False


# Attendance Event CRUD operations
def get_attendance_event(db: Session, event_id: int) -> Optional[models.AttendanceEvent]:
    return db.query(models.AttendanceEvent).filter(models.AttendanceEvent.id == event_id).first()


def get_attendance_events(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    student_id: Optional[int] = None,
    camera_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> List[models.AttendanceEvent]:
    query = db.query(models.AttendanceEvent)

    if student_id:
        query = query.filter(models.AttendanceEvent.student_id == student_id)
    if camera_id:
        query = query.filter(models.AttendanceEvent.camera_id == camera_id)
    if start_date:
        query = query.filter(models.AttendanceEvent.timestamp >= start_date)
    if end_date:
        query = query.filter(models.AttendanceEvent.timestamp <= end_date)

    return query.order_by(desc(models.AttendanceEvent.timestamp)).offset(skip).limit(limit).all()


def create_attendance_event(db: Session, event: AttendanceEventCreate) -> models.AttendanceEvent:
    db_event = models.AttendanceEvent(**event.model_dump())
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


# Dashboard Statistics
def get_dashboard_stats(db: Session) -> dict:
    """Get dashboard statistics. "Today"/"this week" are local (server) calendar days;
    event timestamps are stored as naive UTC and converted for the comparison."""
    live_student = models.Student.deleted_at.is_(None)
    live_camera = models.Camera.deleted_at.is_(None)

    # Total students (soft-deleted students excluded)
    total_students = db.query(func.count(models.Student.id)).filter(live_student).scalar()

    # Active students
    active_students = db.query(func.count(models.Student.id)).filter(
        live_student, models.Student.is_active == True
    ).scalar()

    # Total cameras (soft-deleted cameras excluded)
    total_cameras = db.query(func.count(models.Camera.id)).filter(live_camera).scalar()

    # Active cameras
    active_cameras = db.query(func.count(models.Camera.id)).filter(
        live_camera, models.Camera.is_active == True
    ).scalar()

    # IN and OUT camera counts
    in_cameras = db.query(func.count(models.Camera.id)).filter(
        and_(live_camera, models.Camera.is_active == True, models.Camera.camera_type == "IN")
    ).scalar()

    out_cameras = db.query(func.count(models.Camera.id)).filter(
        and_(live_camera, models.Camera.is_active == True, models.Camera.camera_type == "OUT")
    ).scalar()

    # Attendance today (number of events)
    today = date.today()
    today_start = local_day_start_utc(today)
    attendance_today = db.query(func.count(models.AttendanceEvent.id)).filter(
        timestamp_at_or_after(models.AttendanceEvent.timestamp, today_start)
    ).scalar()

    # Today's entries and exits
    today_entries = db.query(func.count(models.AttendanceEvent.id)).filter(
        and_(
            timestamp_at_or_after(models.AttendanceEvent.timestamp, today_start),
            models.AttendanceEvent.event_type == "ENTRY"
        )
    ).scalar()

    today_exits = db.query(func.count(models.AttendanceEvent.id)).filter(
        and_(
            timestamp_at_or_after(models.AttendanceEvent.timestamp, today_start),
            models.AttendanceEvent.event_type == "EXIT"
        )
    ).scalar()

    # Who is inside now: people whose latest event today is an ENTRY (from stored events,
    # so it survives restarts). Replaces the old "entries - exits" estimate, which an EXIT
    # without an ENTRY (or two ENTRYs) could skew.
    inside = people_inside_today(db, today_start)
    people = {s.id: s for s in db.query(models.Student).filter(models.Student.id.in_(set(inside))).all()} if inside else {}
    estimated_present = len(inside)
    students_inside = sum(1 for sid in inside if people.get(sid) is not None and people[sid].person_type == "student")
    staff_inside = sum(1 for sid in inside if people.get(sid) is not None and people[sid].person_type == "staff")

    # Distinct registered students (not staff) with at least one ENTRY today
    present_today = db.query(func.count(func.distinct(models.AttendanceEvent.student_id))).join(
        models.Student, models.AttendanceEvent.student_id == models.Student.id
    ).filter(
        timestamp_at_or_after(models.AttendanceEvent.timestamp, today_start),
        models.AttendanceEvent.event_type == "ENTRY",
        models.Student.person_type == "student",
    ).scalar()

    student_count = db.query(func.count(models.Student.id)).filter(
        live_student, models.Student.is_active == True, models.Student.person_type == "student").scalar()
    staff_count = db.query(func.count(models.Student.id)).filter(
        live_student, models.Student.is_active == True, models.Student.person_type == "staff").scalar()
    # Only faces the active recognition model can use (its encodings have a fixed byte size)
    from app import face_models
    enrolled_people = db.query(func.count(func.distinct(models.FaceEmbedding.student_id))).join(
        models.Student, models.FaceEmbedding.student_id == models.Student.id
    ).filter(live_student, models.Student.is_active == True, models.FaceEmbedding.is_active == True,
             func.length(models.FaceEmbedding.embedding_data) == face_models.encoded_size()).scalar()

    # Occupancy per classroom: each person inside is attributed to the classroom of the
    # camera that recorded their latest ENTRY (unassigned cameras -> no classroom)
    camera_rooms = {c.id: c.classroom_id for c in db.query(models.Camera).all()}
    rooms = {r.id: r.name for r in db.query(models.Classroom).filter(models.Classroom.deleted_at.is_(None)).all()}
    per_room: dict = {}
    for camera_id in inside.values():
        room_id = camera_rooms.get(camera_id)
        per_room[room_id] = per_room.get(room_id, 0) + 1
    classroom_occupancy = [{"classroom_id": rid, "name": rooms.get(rid, "Unassigned") if rid else "Unassigned",
                            "known_inside": n} for rid, n in sorted(per_room.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))]
    for rid, name in rooms.items():
        if rid not in per_room:
            classroom_occupancy.append({"classroom_id": rid, "name": name, "known_inside": 0})

    # Attendance this week (local Monday 00:00)
    week_start = local_day_start_utc(today - timedelta(days=today.weekday()))
    attendance_this_week = db.query(func.count(models.AttendanceEvent.id)).filter(
        timestamp_at_or_after(models.AttendanceEvent.timestamp, week_start)
    ).scalar()

    # real-time data from active video processing sessions
    from app.routers.video import active_sessions, occupancy_snapshot
    total_current_inside = 0
    total_known_recognitions = 0
    total_unknown_seen = 0
    cameras_processing = 0
    cameras_unhealthy = 0

    for session_info in list(active_sessions.values()):
        if session_info.get("starting"):
            continue
        service = session_info['service']
        stats = service.get_stats()
        # current_inside_count comes from the shared attendance engine, so every
        # session reports the same global value - take it once, don't sum it
        total_current_inside = max(total_current_inside, stats.get('current_inside_count', 0))
        total_known_recognitions += stats.get('total_known_recognitions', 0)
        total_unknown_seen += stats.get('total_unknown_seen', 0)
        if service.is_running:
            cameras_processing += 1
        if not stats.get('camera_healthy', True) or stats.get('stale') or stats.get('pipeline_error'):
            cameras_unhealthy += 1

    # Unregistered people: one shared in-memory tracker for all cameras (never summed)
    occupancy = occupancy_snapshot()

    result = {
        "total_students": total_students,
        "active_students": active_students,
        "total_cameras": total_cameras,
        "active_cameras": active_cameras,
        "in_cameras": in_cameras,
        "out_cameras": out_cameras,
        "attendance_today": attendance_today,
        "today_entries": today_entries,
        "today_exits": today_exits,
        "estimated_present": estimated_present,
        "attendance_this_week": attendance_this_week,
        "present_today": present_today,
        "student_count": student_count,
        "staff_count": staff_count,
        "enrolled_people": enrolled_people,
        "known_inside_count": estimated_present,
        "students_inside": students_inside,
        "staff_inside": staff_inside,
        "unknown_inside_count": occupancy["unknown_inside_count"],
        "unknown_entries_today": occupancy["unknown_entries_today"],
        "unknown_tracking_since": occupancy["unknown_tracking_since"],
        "unknown_tracking_active": occupancy["unknown_tracking_active"],
        "cameras_processing": cameras_processing,
        "cameras_unhealthy": cameras_unhealthy,
        "classroom_occupancy": classroom_occupancy,
    }

    # Add real-time processing data if any cameras are active
    if cameras_processing > 0:
        result.update({
            "current_inside_count": total_current_inside,
            "total_known_recognitions": total_known_recognitions,
            "total_unknown_seen": total_unknown_seen
        })

    return result


def people_inside_today(db: Session, today_start: datetime) -> dict:
    """{student db id: camera id of their latest ENTRY} for everyone whose latest event
    today is an ENTRY"""
    rows = db.query(models.AttendanceEvent.student_id, models.AttendanceEvent.event_type,
                    models.AttendanceEvent.camera_id).filter(
        timestamp_at_or_after(models.AttendanceEvent.timestamp, today_start)
    ).order_by(models.AttendanceEvent.timestamp, models.AttendanceEvent.id).all()
    latest: dict = {}
    for student_id, event_type, camera_id in rows:
        latest[student_id] = (event_type, camera_id)
    return {sid: camera_id for sid, (event_type, camera_id) in latest.items() if event_type == "ENTRY"}


def get_recent_attendance(db: Session, limit: int = 50, camera_id: Optional[int] = None) -> List[models.AttendanceEvent]:
    """Get recent attendance events with student and camera details (newest first).
    Events of soft-deleted students/cameras are included: history is preserved."""
    query = db.query(models.AttendanceEvent).join(
        models.Student, models.AttendanceEvent.student_id == models.Student.id
    ).outerjoin(
        models.Camera, models.AttendanceEvent.camera_id == models.Camera.id
    ).options(
        contains_eager(models.AttendanceEvent.student),
        contains_eager(models.AttendanceEvent.camera)
    )
    if camera_id is not None:
        query = query.filter(models.AttendanceEvent.camera_id == camera_id)
    return query.order_by(desc(models.AttendanceEvent.timestamp), desc(models.AttendanceEvent.id)).limit(limit).all()


# --- Time helpers ------------------------------------------------------------
# attendance_events.timestamp is written by the database (SQLite CURRENT_TIMESTAMP),
# i.e. naive UTC. Calendar days are the server's local days.

def local_day_start_utc(day: date) -> datetime:
    """Local midnight of `day` as naive UTC, for comparing with stored timestamps"""
    local_midnight = datetime.combine(day, dtime.min).astimezone()  # naive -> local aware
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


# Stored values come in two text formats: "YYYY-MM-DD HH:MM:SS" (written by SQLite's
# CURRENT_TIMESTAMP) and "YYYY-MM-DD HH:MM:SS.ffffff" (written by SQLAlchemy). SQLite
# compares them as text, and a SQLAlchemy datetime parameter is bound with
# ".000000", so an event at exactly 00:00:00 sorted *before* the day's start bound and
# was counted on the previous day. Binding the bound in the seconds-only format puts
# both stored formats on the correct side. The column is not wrapped in a function,
# so ix_attendance_events_timestamp is still usable.
STORED_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _timestamp_bound(dt: datetime) -> str:
    """Naive-UTC bound as text in the stored seconds-only format"""
    return dt.strftime(STORED_TIMESTAMP_FORMAT)


def timestamp_at_or_after(column, dt: datetime):
    """column >= dt (dt: naive UTC, whole seconds) with correct text ordering on SQLite"""
    return type_coerce(column, String) >= _timestamp_bound(dt)


def timestamp_before(column, dt: datetime):
    """column < dt (dt: naive UTC, whole seconds) with correct text ordering on SQLite"""
    return type_coerce(column, String) < _timestamp_bound(dt)


def as_utc(ts: datetime) -> datetime:
    """Stored (naive UTC) timestamp -> timezone-aware UTC"""
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def to_local(ts: datetime) -> datetime:
    return as_utc(ts).astimezone()


# --- Daily attendance & trend ------------------------------------------------

def get_daily_attendance(db: Session, days: int = 7, late_after: Optional[dtime] = None,
                         end_day: Optional[date] = None) -> List[dict]:
    """Per local day (oldest first), ending at end_day (default today):
      present - distinct students whose first ENTRY that day is on time
      late    - distinct students whose first ENTRY is after late_after (0 if not configured)
      absent  - current active, non-deleted students with no ENTRY that day
      total   - present + late + absent
    The roster is today's active students; per-day historical rosters are not stored.
    """
    end_day = end_day or date.today()
    first_day = end_day - timedelta(days=days - 1)

    # Students only: staff entries are not class attendance
    entries = db.query(models.AttendanceEvent.student_id, models.AttendanceEvent.timestamp).join(
        models.Student, models.AttendanceEvent.student_id == models.Student.id
    ).filter(
        models.Student.person_type == "student",
        models.AttendanceEvent.event_type == "ENTRY",
        timestamp_at_or_after(models.AttendanceEvent.timestamp, local_day_start_utc(first_day)),
        timestamp_before(models.AttendanceEvent.timestamp, local_day_start_utc(end_day + timedelta(days=1))),
    ).all()

    first_entry: dict = {}  # (local date, student_id) -> earliest local time
    for student_id, ts in entries:
        if ts is None:
            continue
        local = to_local(ts)
        key = (local.date(), student_id)
        if key not in first_entry or local.time() < first_entry[key]:
            first_entry[key] = local.time()

    roster = db.query(func.count(models.Student.id)).filter(
        models.Student.deleted_at.is_(None), models.Student.is_active == True,
        models.Student.person_type == "student"
    ).scalar()

    result = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        arrivals = [t for (d, _), t in first_entry.items() if d == day]
        late = sum(1 for t in arrivals if late_after is not None and t > late_after)
        present = len(arrivals) - late
        absent = max(0, roster - len(arrivals))
        result.append({"date": day, "present": present, "absent": absent, "late": late,
                       "total": present + late + absent})
    return result


def get_attendance_trend(db: Session, period_days: int = 14, late_after: Optional[dtime] = None) -> dict:
    """Compare average daily attendees (present + late) of the last half of the
    period with the first half. Uses completed days only (ends yesterday).
    change_percentage is the relative change in %; |change| < 1% is "stable"."""
    half = max(1, period_days // 2)
    daily = get_daily_attendance(db, days=half * 2, late_after=late_after,
                                 end_day=date.today() - timedelta(days=1))
    previous = sum(d["present"] + d["late"] for d in daily[:half]) / half
    recent = sum(d["present"] + d["late"] for d in daily[half:]) / half

    if previous > 0:
        change = (recent - previous) / previous * 100.0
    else:
        change = 100.0 if recent > 0 else 0.0

    trend = "up" if change >= 1.0 else ("down" if change <= -1.0 else "stable")
    return {"trend": trend, "change_percentage": round(change, 1), "period_days": half * 2}