"""
Course enrollment router (student <-> course for a semester), backed by the
`enrollments` table. Request/response shapes are unchanged from the previous
in-memory implementation.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.config import settings
from app.database import get_db
from app.permissions import require_operator, require_viewer
from app import models
from app.schemas import UTCDateTime
from pydantic import BaseModel
from datetime import datetime, date

router = APIRouter()

# Pydantic models
class EnrollmentBase(BaseModel):
    student_id: int
    course_code: str
    course_name: str
    semester: str
    section: str | None = None
    enrolled_date: date | None = None
    is_active: bool = True

class EnrollmentCreate(EnrollmentBase):
    pass

class Enrollment(EnrollmentBase):
    id: int
    created_at: UTCDateTime
    updated_at: UTCDateTime | None = None

    class Config:
        from_attributes = True


def get_enrollment_or_404(db: Session, enrollment_id: int) -> models.Enrollment:
    enrollment = db.query(models.Enrollment).filter(models.Enrollment.id == enrollment_id).first()
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    return enrollment


def validate_enrollment(db: Session, enrollment: EnrollmentCreate, exclude_id: Optional[int] = None):
    if enrollment.student_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid student ID")
    student = db.query(models.Student).filter(
        models.Student.id == enrollment.student_id, models.Student.deleted_at.is_(None)
    ).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")

    # Check if already enrolled in this course/semester
    if enrollment.is_active:
        duplicate = db.query(models.Enrollment).filter(
            models.Enrollment.student_id == enrollment.student_id,
            models.Enrollment.course_code == enrollment.course_code,
            models.Enrollment.semester == enrollment.semester,
            models.Enrollment.is_active == True,
        )
        if exclude_id is not None:
            duplicate = duplicate.filter(models.Enrollment.id != exclude_id)
        if duplicate.first() is not None:
            raise HTTPException(
                status_code=400,
                detail="Student already enrolled in this course for the semester"
            )


@router.get("/", response_model=List[Enrollment])
async def read_enrollments(
    skip: int = 0,
    limit: int = 100,
    student_id: Optional[int] = None,
    course_code: Optional[str] = None,
    semester: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user=Depends(require_viewer)
):
    query = db.query(models.Enrollment)

    if student_id:
        query = query.filter(models.Enrollment.student_id == student_id)
    if course_code:
        query = query.filter(models.Enrollment.course_code == course_code)
    if semester:
        query = query.filter(models.Enrollment.semester == semester)
    if is_active is not None:
        query = query.filter(models.Enrollment.is_active == is_active)

    return query.order_by(models.Enrollment.id).offset(skip).limit(limit).all()

@router.post("/", response_model=Enrollment)
async def create_enrollment(enrollment: EnrollmentCreate, db: Session = Depends(get_db), current_user=Depends(require_operator)):
    validate_enrollment(db, enrollment)
    db_enrollment = models.Enrollment(**enrollment.model_dump())
    db.add(db_enrollment)
    db.commit()
    db.refresh(db_enrollment)
    return db_enrollment

@router.get("/{enrollment_id}", response_model=Enrollment)
async def read_enrollment(enrollment_id: int, db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    return get_enrollment_or_404(db, enrollment_id)

@router.put("/{enrollment_id}", response_model=Enrollment)
async def update_enrollment(enrollment_id: int, enrollment: EnrollmentCreate, db: Session = Depends(get_db), current_user=Depends(require_operator)):
    db_enrollment = get_enrollment_or_404(db, enrollment_id)
    validate_enrollment(db, enrollment, exclude_id=enrollment_id)
    for field, value in enrollment.model_dump().items():
        setattr(db_enrollment, field, value)
    db.commit()
    db.refresh(db_enrollment)
    return db_enrollment

@router.delete("/{enrollment_id}")
async def delete_enrollment(enrollment_id: int, db: Session = Depends(get_db), current_user=Depends(require_operator)):
    db_enrollment = get_enrollment_or_404(db, enrollment_id)
    # Soft delete - mark as inactive
    db_enrollment.is_active = False
    db.commit()
    return {"message": "Enrollment deleted successfully"}

@router.get("/student/{student_id}", response_model=List[Enrollment])
async def get_student_enrollments(student_id: int, db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    return db.query(models.Enrollment).filter(
        models.Enrollment.student_id == student_id,
        models.Enrollment.is_active == True
    ).order_by(models.Enrollment.id).all()
