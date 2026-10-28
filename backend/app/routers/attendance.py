from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session
from typing import List, Optional
from app.config import settings
from app.database import get_db
from app import attendance_logic, crud, models
from app.permissions import audit, require_admin, require_operator, require_viewer
from app.schemas import (AttendanceEventResponse as AttendanceEventSchema,
                         AttendanceEventCreate as AttendanceEventCreateSchema, AttendanceRecord, EventType,
                         StrippedStr)
from datetime import datetime, date, timedelta, timezone

router = APIRouter()

MAX_RANGE_DAYS = 366


def check_range(start_date: Optional[date], end_date: Optional[date]):
    if start_date and end_date:
        if end_date < start_date:
            raise HTTPException(status_code=400, detail="end_date must not be before start_date")
        if (end_date - start_date).days >= MAX_RANGE_DAYS:
            raise HTTPException(status_code=400, detail=f"Date range is limited to {MAX_RANGE_DAYS} days")


@router.get("/", response_model=List[AttendanceEventSchema])
async def read_attendance(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=5000),
    student_id: Optional[int] = None,
    camera_id: Optional[int] = None,
    start_date: Optional[date] = Query(None, description="First local day, inclusive"),
    end_date: Optional[date] = Query(None, description="Last local day, inclusive"),
    db: Session = Depends(get_db),
    current_user=Depends(require_viewer)
):
    """Raw events, newest first. Dates are local calendar days and both ends are
    inclusive (end_date=2026-10-19 includes events up to 23:59:59 that day)."""
    check_range(start_date, end_date)
    query = db.query(models.AttendanceEvent)

    if student_id:
        query = query.filter(models.AttendanceEvent.student_id == student_id)
    if camera_id:
        query = query.filter(models.AttendanceEvent.camera_id == camera_id)
    if start_date:
        query = query.filter(crud.timestamp_at_or_after(models.AttendanceEvent.timestamp,
                                                        crud.local_day_start_utc(start_date)))
    if end_date:
        query = query.filter(crud.timestamp_before(models.AttendanceEvent.timestamp,
                                                   crud.local_day_start_utc(end_date + timedelta(days=1))))

    return query.order_by(desc(models.AttendanceEvent.timestamp), desc(models.AttendanceEvent.id)).offset(skip).limit(limit).all()


@router.get("/sessions")
async def read_presence_sessions(
    start_date: date = Query(..., description="First local day, inclusive"),
    end_date: date = Query(..., description="Last local day, inclusive"),
    student_id: Optional[int] = None,
    q: Optional[str] = Query(None, max_length=100, description="Search name or ID"),
    class_name: Optional[str] = None,
    person_type: Optional[str] = Query(None, pattern="^(student|staff)$"),
    db: Session = Depends(get_db),
    current_user=Depends(require_viewer)
):
    """ENTRY/EXIT pairs with time inside, per person per day (see
    attendance_logic.presence_sessions). Unpaired events are flagged, not dropped."""
    check_range(start_date, end_date)
    sessions = attendance_logic.presence_sessions(db, start_date, end_date, student_id=student_id)
    if q:
        needle = q.strip().lower()
        sessions = [s for s in sessions if needle in (s["name"] or "").lower() or needle in (s["student_id"] or "").lower()]
    if class_name:
        sessions = [s for s in sessions if s["class_name"] == class_name]
    if person_type:
        sessions = [s for s in sessions if s["person_type"] == person_type]
    return sessions


class ManualEvent(BaseModel):
    student_id: int
    event_type: EventType
    timestamp: datetime = Field(..., description="When it happened; ISO 8601 with offset, or local time if naive")
    camera_id: Optional[int] = None
    reason: StrippedStr = Field(..., min_length=3, max_length=500)


@router.post("/manual", response_model=AttendanceEventSchema)
async def create_manual_event(event: ManualEvent, db: Session = Depends(get_db), current_user=Depends(require_operator)):
    """Record an ENTRY/EXIT the cameras missed (or a correction). Stored with
    source="manual", the operator and the reason, and audited."""
    if crud.get_live_student(db, event.student_id) is None:
        raise HTTPException(status_code=400, detail=f"Student {event.student_id} does not exist or has been deleted")
    if event.camera_id is not None and crud.get_live_camera(db, event.camera_id) is None:
        raise HTTPException(status_code=400, detail=f"Camera {event.camera_id} does not exist or has been deleted")
    ts = event.timestamp if event.timestamp.tzinfo else event.timestamp.astimezone()  # naive = server-local
    ts_utc = ts.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
    if ts_utc > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5):
        raise HTTPException(status_code=400, detail="A manual event cannot be in the future")
    row = models.AttendanceEvent(student_id=event.student_id, camera_id=event.camera_id,
                                 event_type=event.event_type.value, timestamp=ts_utc, confidence=100,
                                 source="manual", created_by_user_id=getattr(current_user, "id", None),
                                 note=event.reason)
    db.add(row)
    db.flush()
    audit(db, current_user, "attendance.manual_event", "attendance_event", row.id,
          {"student_db_id": event.student_id, "event_type": event.event_type.value, "timestamp": ts_utc,
           "reason": event.reason})
    db.commit()
    db.refresh(row)
    return row


@router.get("/recent", response_model=List[AttendanceRecord])
@router.get("/recent/", response_model=List[AttendanceRecord], include_in_schema=False)
async def read_recent_attendance(
    limit: int = Query(50, ge=1, le=500),
    camera_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_viewer)
):
    """Newest attendance events with student and camera names.
    Declared before /{attendance_id} so "recent" is not parsed as an ID."""
    events = crud.get_recent_attendance(db, limit=limit, camera_id=camera_id)
    return [
        AttendanceRecord(
            id=event.id,
            student_name=event.student.name,
            student_id=event.student.student_id,
            event_type=event.event_type,
            timestamp=crud.as_utc(event.timestamp),
            confidence=event.confidence,
            camera_name=event.camera.name if event.camera else None,
            student_db_id=event.student_id,
            camera_id=event.camera_id,
            source=event.source or "camera",
            person_type=event.student.person_type,
        )
        for event in events
    ]


def validate_event_references(db: Session, attendance: AttendanceEventCreateSchema):
    """Reject events for students/cameras that don't exist or were (soft) deleted.
    400 because the problem is in the request body, not the URL."""
    if crud.get_live_student(db, attendance.student_id) is None:
        raise HTTPException(status_code=400, detail=f"Student {attendance.student_id} does not exist or has been deleted")
    if attendance.camera_id is not None and crud.get_live_camera(db, attendance.camera_id) is None:
        raise HTTPException(status_code=400, detail=f"Camera {attendance.camera_id} does not exist or has been deleted")


@router.post("/", response_model=AttendanceEventSchema)
async def create_attendance(attendance: AttendanceEventCreateSchema, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """Low-level event creation (admin, audited). Operators use POST /attendance/manual,
    which records who made the entry and why."""
    validate_event_references(db, attendance)
    db_attendance = models.AttendanceEvent(**attendance.model_dump(mode="json"), source="manual",
                                           created_by_user_id=getattr(current_user, "id", None))
    db.add(db_attendance)
    db.flush()
    audit(db, current_user, "attendance.create", "attendance_event", db_attendance.id,
          attendance.model_dump(mode="json"))
    db.commit()
    db.refresh(db_attendance)
    return db_attendance


@router.get("/{attendance_id}", response_model=AttendanceEventSchema)
async def read_attendance_record(attendance_id: int, db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    attendance_event = db.query(models.AttendanceEvent).filter(models.AttendanceEvent.id == attendance_id).first()
    if attendance_event is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    return attendance_event


@router.put("/{attendance_id}", response_model=AttendanceEventSchema)
async def update_attendance(attendance_id: int, attendance: AttendanceEventCreateSchema, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    db_attendance = db.query(models.AttendanceEvent).filter(models.AttendanceEvent.id == attendance_id).first()
    if db_attendance is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    validate_event_references(db, attendance)

    update_data = attendance.model_dump(exclude_unset=True, mode="json")
    before = {field: getattr(db_attendance, field) for field in update_data}
    for field, value in update_data.items():
        setattr(db_attendance, field, value)
    # A camera event whose person, type or time was changed by hand is no longer a camera
    # recognition: label it manual so reports and the UI never present it as one
    if (db_attendance.source or "camera") != "manual" and any(
            str(before[f]) != str(update_data[f]) for f in ("student_id", "event_type", "timestamp") if f in update_data):
        before["source"] = db_attendance.source
        db_attendance.source = "manual"
        editor = getattr(current_user, "username", None) or "an administrator"
        db_attendance.note = (f"{db_attendance.note}; " if db_attendance.note else "") + f"camera event edited by {editor}"
        update_data = {**update_data, "source": "manual"}
    audit(db, current_user, "attendance.update", "attendance_event", attendance_id,
          {"before": before, "after": update_data})

    db.commit()
    db.refresh(db_attendance)
    return db_attendance


@router.delete("/{attendance_id}")
async def delete_attendance(attendance_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    db_attendance = db.query(models.AttendanceEvent).filter(models.AttendanceEvent.id == attendance_id).first()
    if db_attendance is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    # The audit entry keeps what was deleted (no face data is involved)
    audit(db, current_user, "attendance.delete", "attendance_event", attendance_id,
          {"student_db_id": db_attendance.student_id, "camera_id": db_attendance.camera_id,
           "event_type": db_attendance.event_type, "timestamp": db_attendance.timestamp,
           "source": db_attendance.source})
    db.delete(db_attendance)
    db.commit()
    return {"message": "Attendance record deleted successfully"}


@router.get("/student/{student_id}/today", response_model=List[AttendanceEventSchema])
async def get_today_attendance(student_id: int, db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    # Local calendar day, converted to the naive-UTC format timestamps are stored in
    today_start = crud.local_day_start_utc(date.today())
    tomorrow_start = crud.local_day_start_utc(date.today() + timedelta(days=1))

    todays_attendance = db.query(models.AttendanceEvent).filter(
        models.AttendanceEvent.student_id == student_id,
        crud.timestamp_at_or_after(models.AttendanceEvent.timestamp, today_start),
        crud.timestamp_before(models.AttendanceEvent.timestamp, tomorrow_start)
    ).all()

    return todays_attendance