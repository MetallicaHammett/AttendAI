"""
Course-wise attendance for one student: the student's own page (student accounts) and
the same view of any student for staff.

A student account can reach only GET /portal/me/attendance. Which student it shows comes
from the account's link in the database (users.student_db_id), never from the request.
"""
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import attendance_logic, models
from app.attendance_logic import stored_to_local_naive
from app.database import get_db
from app.permissions import require_student, require_viewer

router = APIRouter()

MAX_RANGE_DAYS = 366


def _range(db: Session, student: models.Student, start_date: Optional[date], end_date: Optional[date]):
    """Default: from the day the student's earliest current period was added to the
    timetable (at most a year back) to today"""
    today = datetime.now().date()
    end_day = end_date or today
    if start_date is None:
        first = db.query(func.min(models.TimetablePeriod.created_at)).filter(
            models.TimetablePeriod.class_name == student.class_name,
            models.TimetablePeriod.is_active == True).scalar()  # noqa: E712
        start_date = stored_to_local_naive(first).date() if first is not None else end_day
        start_date = max(start_date, end_day - timedelta(days=MAX_RANGE_DAYS - 1))
    if start_date > end_day:
        raise HTTPException(status_code=400, detail="The start date must not be after the end date")
    if (end_day - start_date).days >= MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"Choose a range of at most {MAX_RANGE_DAYS} days")
    return start_date, end_day


def _summary(db: Session, student: models.Student, start_date: Optional[date], end_date: Optional[date]):
    start_day, end_day = _range(db, student, start_date, end_date)
    return attendance_logic.student_course_summary(db, student, start_day, end_day)


@router.get("/me/attendance")
async def my_attendance(start_date: Optional[date] = Query(None), end_date: Optional[date] = Query(None),
                        db: Session = Depends(get_db), current_user=Depends(require_student)):
    account = db.query(models.User).filter(models.User.id == current_user.id).first()
    student = None
    if account is not None and account.student_db_id is not None:
        student = db.query(models.Student).filter(models.Student.id == account.student_db_id,
                                                  models.Student.deleted_at.is_(None)).first()
    if student is None:
        raise HTTPException(status_code=404, detail="This account is not linked to a student record. "
                                                    "Ask an administrator to check your account.")
    return _summary(db, student, start_date, end_date)


@router.get("/students/{student_db_id}/attendance")
async def student_attendance(student_db_id: int, start_date: Optional[date] = Query(None),
                             end_date: Optional[date] = Query(None), db: Session = Depends(get_db),
                             current_user=Depends(require_viewer)):
    student = db.query(models.Student).filter(models.Student.id == student_db_id,
                                              models.Student.person_type == "student",
                                              models.Student.deleted_at.is_(None)).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    return _summary(db, student, start_date, end_date)
