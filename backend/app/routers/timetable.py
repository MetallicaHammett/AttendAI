"""
Classrooms, weekly timetable periods, holidays, period attendance and overrides.
"""
from datetime import date, datetime, time, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app import attendance_logic, models
from app.database import get_db
from app.permissions import audit, require_admin, require_operator, require_viewer
from app.schemas import StrippedStr, UTCDateTime

router = APIRouter()

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


# --- schemas --------------------------------------------------------------------------

class ClassroomIn(BaseModel):
    name: StrippedStr = Field(..., max_length=100)
    building: Optional[str] = Field(None, max_length=100)
    capacity: Optional[int] = Field(None, ge=1, le=10000)
    description: Optional[str] = Field(None, max_length=1000)
    is_active: bool = True


class ClassroomOut(ClassroomIn):
    id: int
    created_at: Optional[UTCDateTime] = None

    class Config:
        from_attributes = True


class PeriodIn(BaseModel):
    classroom_id: int
    class_name: StrippedStr = Field(..., max_length=100)
    course_code: Optional[str] = Field(None, max_length=50)
    course_name: StrippedStr = Field(..., max_length=200)
    day_of_week: int = Field(..., ge=0, le=6, description="0 = Monday ... 6 = Sunday")
    start_time: time
    end_time: time
    grace_minutes: Optional[int] = Field(None, ge=0, le=120)
    is_active: bool = True
    # A "professor" account; they then see this period in the staff portal
    professor_user_id: Optional[int] = Field(None, gt=0)

    @model_validator(mode="after")
    def check_times(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class PeriodOut(PeriodIn):
    id: int
    day_name: str = ""
    professor_username: Optional[str] = None

    class Config:
        from_attributes = True


class HolidayIn(BaseModel):
    date: date
    name: StrippedStr = Field(..., max_length=200)


class HolidayOut(HolidayIn):
    id: int

    class Config:
        from_attributes = True


class OverrideIn(BaseModel):
    status: str = Field(..., pattern="^(PRESENT|LATE|ABSENT|EXCUSED)$")
    reason: StrippedStr = Field(..., min_length=3, max_length=500)


# --- classrooms -----------------------------------------------------------------------

def _classroom_or_404(db: Session, classroom_id: int) -> models.Classroom:
    room = db.query(models.Classroom).filter(models.Classroom.id == classroom_id,
                                             models.Classroom.deleted_at.is_(None)).first()
    if room is None:
        raise HTTPException(status_code=404, detail="Classroom not found")
    return room


@router.get("/classrooms", response_model=List[ClassroomOut])
async def list_classrooms(db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    return db.query(models.Classroom).filter(models.Classroom.deleted_at.is_(None)).order_by(models.Classroom.name).all()


@router.post("/classrooms", response_model=ClassroomOut)
async def create_classroom(room: ClassroomIn, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    if db.query(models.Classroom).filter(models.Classroom.name == room.name).first():
        raise HTTPException(status_code=400, detail="A classroom with this name already exists")
    row = models.Classroom(**room.model_dump())
    db.add(row)
    db.flush()
    audit(db, current_user, "classroom.create", "classroom", row.id, {"name": row.name})
    db.commit()
    db.refresh(row)
    return row


@router.put("/classrooms/{classroom_id}", response_model=ClassroomOut)
async def update_classroom(classroom_id: int, room: ClassroomIn, db: Session = Depends(get_db),
                           current_user=Depends(require_admin)):
    row = _classroom_or_404(db, classroom_id)
    clash = db.query(models.Classroom).filter(models.Classroom.name == room.name,
                                              models.Classroom.id != classroom_id).first()
    if clash:
        raise HTTPException(status_code=400, detail="A classroom with this name already exists")
    for key, value in room.model_dump().items():
        setattr(row, key, value)
    audit(db, current_user, "classroom.update", "classroom", row.id, {"name": row.name})
    db.commit()
    db.refresh(row)
    return row


@router.delete("/classrooms/{classroom_id}")
async def delete_classroom(classroom_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """Soft delete. Refused while cameras or active periods still use the classroom."""
    row = _classroom_or_404(db, classroom_id)
    if db.query(models.Camera).filter(models.Camera.classroom_id == classroom_id,
                                      models.Camera.deleted_at.is_(None)).count():
        raise HTTPException(status_code=409, detail="Reassign or delete this classroom's cameras first")
    if db.query(models.TimetablePeriod).filter(models.TimetablePeriod.classroom_id == classroom_id,
                                               models.TimetablePeriod.is_active == True).count():  # noqa: E712
        raise HTTPException(status_code=409, detail="Deactivate this classroom's timetable periods first")
    row.deleted_at = datetime.now(timezone.utc)
    row.is_active = False
    audit(db, current_user, "classroom.delete", "classroom", row.id, {"name": row.name})
    db.commit()
    return {"message": "Classroom deleted"}


# --- timetable periods ------------------------------------------------------------------

def _period_out(p: models.TimetablePeriod) -> PeriodOut:
    out = PeriodOut.model_validate(p)
    out.day_name = DAY_NAMES[p.day_of_week]
    out.professor_username = p.professor.username if p.professor is not None else None
    return out


def _check_professor(db: Session, user_id: Optional[int]):
    """Only an active professor account can be assigned to teach a period"""
    if user_id is None:
        return
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None or user.role != models.PROFESSOR_ROLE or not user.is_active:
        raise HTTPException(status_code=400, detail="Choose an active professor account to teach this period")


def _check_overlap(db: Session, period: PeriodIn, exclude_id: Optional[int] = None):
    """Overlapping active periods for the same class group or the same classroom on the
    same weekday are rejected: one student cannot be in two places, one room cannot
    host two classes."""
    if not period.is_active:
        return
    query = db.query(models.TimetablePeriod).filter(
        models.TimetablePeriod.day_of_week == period.day_of_week,
        models.TimetablePeriod.is_active == True,  # noqa: E712
        models.TimetablePeriod.start_time < period.end_time,
        models.TimetablePeriod.end_time > period.start_time,
    )
    if exclude_id is not None:
        query = query.filter(models.TimetablePeriod.id != exclude_id)
    for other in query.all():
        if other.class_name == period.class_name:
            raise HTTPException(status_code=409, detail=f"Overlaps {other.course_name} ({other.start_time:%H:%M}-"
                                                        f"{other.end_time:%H:%M}) for class {other.class_name}")
        if other.classroom_id == period.classroom_id:
            raise HTTPException(status_code=409, detail=f"The classroom is already booked for {other.class_name} "
                                                        f"({other.start_time:%H:%M}-{other.end_time:%H:%M})")
        if period.professor_user_id is not None and other.professor_user_id == period.professor_user_id:
            raise HTTPException(status_code=409, detail=f"The professor already teaches {other.class_name} "
                                                        f"({other.start_time:%H:%M}-{other.end_time:%H:%M})")


def _period_or_404(db: Session, period_id: int) -> models.TimetablePeriod:
    period = db.query(models.TimetablePeriod).filter(models.TimetablePeriod.id == period_id).first()
    if period is None:
        raise HTTPException(status_code=404, detail="Period not found")
    return period


@router.get("/periods", response_model=List[PeriodOut])
async def list_periods(class_name: Optional[str] = None, classroom_id: Optional[int] = None,
                       day_of_week: Optional[int] = Query(None, ge=0, le=6), include_inactive: bool = False,
                       db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    query = db.query(models.TimetablePeriod)
    if not include_inactive:
        query = query.filter(models.TimetablePeriod.is_active == True)  # noqa: E712
    if class_name:
        query = query.filter(models.TimetablePeriod.class_name == class_name)
    if classroom_id:
        query = query.filter(models.TimetablePeriod.classroom_id == classroom_id)
    if day_of_week is not None:
        query = query.filter(models.TimetablePeriod.day_of_week == day_of_week)
    rows = query.order_by(models.TimetablePeriod.day_of_week, models.TimetablePeriod.start_time).all()
    return [_period_out(p) for p in rows]


@router.post("/periods", response_model=PeriodOut)
async def create_period(period: PeriodIn, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    _classroom_or_404(db, period.classroom_id)
    _check_professor(db, period.professor_user_id)
    _check_overlap(db, period)
    row = models.TimetablePeriod(**period.model_dump())
    db.add(row)
    db.flush()
    audit(db, current_user, "period.create", "period", row.id, period.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return _period_out(row)


@router.put("/periods/{period_id}", response_model=PeriodOut)
async def update_period(period_id: int, period: PeriodIn, db: Session = Depends(get_db),
                        current_user=Depends(require_admin)):
    row = _period_or_404(db, period_id)
    _classroom_or_404(db, period.classroom_id)
    if period.professor_user_id != row.professor_user_id:  # keeping an existing assignment is always allowed
        _check_professor(db, period.professor_user_id)
    _check_overlap(db, period, exclude_id=period_id)
    for key, value in period.model_dump().items():
        setattr(row, key, value)
    audit(db, current_user, "period.update", "period", row.id, period.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return _period_out(row)


@router.delete("/periods/{period_id}")
async def deactivate_period(period_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """Periods are deactivated, not deleted, so past attendance and overrides keep their reference."""
    row = _period_or_404(db, period_id)
    row.is_active = False
    audit(db, current_user, "period.deactivate", "period", row.id, None)
    db.commit()
    return {"message": "Period deactivated"}


# --- holidays ---------------------------------------------------------------------------

@router.get("/holidays", response_model=List[HolidayOut])
async def list_holidays(year: Optional[int] = Query(None, ge=2000, le=2100), db: Session = Depends(get_db),
                        current_user=Depends(require_viewer)):
    query = db.query(models.Holiday)
    if year:
        query = query.filter(models.Holiday.date >= date(year, 1, 1), models.Holiday.date <= date(year, 12, 31))
    return query.order_by(models.Holiday.date).all()


@router.post("/holidays", response_model=HolidayOut)
async def create_holiday(holiday: HolidayIn, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    if db.query(models.Holiday).filter(models.Holiday.date == holiday.date).first():
        raise HTTPException(status_code=400, detail="A holiday already exists on this date")
    row = models.Holiday(**holiday.model_dump())
    db.add(row)
    db.flush()
    audit(db, current_user, "holiday.create", "holiday", row.id, holiday.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return row


@router.delete("/holidays/{holiday_id}")
async def delete_holiday(holiday_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    row = db.query(models.Holiday).filter(models.Holiday.id == holiday_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Holiday not found")
    audit(db, current_user, "holiday.delete", "holiday", row.id, {"date": row.date, "name": row.name})
    db.delete(row)
    db.commit()
    return {"message": "Holiday deleted"}


# --- period attendance ----------------------------------------------------------------

def _check_day(period: models.TimetablePeriod, day: date):
    if period.created_at is not None:
        added = attendance_logic.stored_to_local_naive(period.created_at).date()
        if day < added:
            raise HTTPException(status_code=400, detail=f"{period.course_name} was added to the timetable on "
                                                        f"{added:%d %b %Y}; it has no attendance before that date")
    if day.weekday() != period.day_of_week:
        raise HTTPException(status_code=400, detail=f"{period.course_name} is scheduled on "
                                                    f"{DAY_NAMES[period.day_of_week]}s, not on {day:%A %d %b %Y}")


@router.get("/periods/{period_id}/attendance")
async def period_attendance(period_id: int, day: date = Query(..., alias="date"), db: Session = Depends(get_db),
                            current_user=Depends(require_viewer)):
    period = _period_or_404(db, period_id)
    _check_day(period, day)
    # Computed with the class's earlier periods that day (break / did-not-return check)
    for result in attendance_logic.compute_day(db, day, class_name=period.class_name):
        if result["period_id"] == period_id:
            return result
    return attendance_logic.compute_period(db, period, day)  # inactive period viewed explicitly


@router.put("/periods/{period_id}/attendance/{student_id}")
async def set_override(period_id: int, student_id: int, override: OverrideIn, day: date = Query(..., alias="date"),
                       db: Session = Depends(get_db), current_user=Depends(require_operator)):
    """Authorised manual decision for one student in one period (audited). Replaces the
    computed status in all reports; the computed status stays visible."""
    period = _period_or_404(db, period_id)
    _check_day(period, day)
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.deleted_at.is_(None)).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    if student.class_name != period.class_name:
        raise HTTPException(status_code=400, detail="The student is not on this period's class roster")
    row = db.query(models.AttendanceOverride).filter_by(period_id=period_id, date=day, student_id=student_id).first()
    before = {"status": row.status, "reason": row.reason} if row else None
    if row is None:
        row = models.AttendanceOverride(period_id=period_id, date=day, student_id=student_id,
                                        status=override.status, reason=override.reason,
                                        created_by_user_id=getattr(current_user, "id", None))
        db.add(row)
    else:
        row.status, row.reason = override.status, override.reason
        row.created_by_user_id = getattr(current_user, "id", None)
    audit(db, current_user, "attendance.override", "period", period_id,
          {"date": day, "student_db_id": student_id, "before": before,
           "after": {"status": override.status, "reason": override.reason}})
    db.commit()
    return {"message": "Override saved", "status": override.status}


@router.delete("/periods/{period_id}/attendance/{student_id}")
async def clear_override(period_id: int, student_id: int, day: date = Query(..., alias="date"),
                         db: Session = Depends(get_db), current_user=Depends(require_operator)):
    row = db.query(models.AttendanceOverride).filter_by(period_id=period_id, date=day, student_id=student_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No override for this student, period and date")
    audit(db, current_user, "attendance.override_cleared", "period", period_id,
          {"date": day, "student_db_id": student_id, "before": {"status": row.status, "reason": row.reason}})
    db.delete(row)
    db.commit()
    return {"message": "Override removed; the computed status applies again"}


@router.get("/daily")
async def daily_summary(day: date = Query(..., alias="date"), class_name: Optional[str] = None,
                        db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    return attendance_logic.daily_summary(db, day, class_name)


@router.get("/monthly")
async def monthly_summary(year: int = Query(..., ge=2000, le=2100), month: int = Query(..., ge=1, le=12),
                          class_name: Optional[str] = None, db: Session = Depends(get_db),
                          current_user=Depends(require_viewer)):
    return attendance_logic.monthly_summary(db, year, month, class_name)
