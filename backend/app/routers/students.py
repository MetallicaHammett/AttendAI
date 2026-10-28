from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload
from typing import List, Optional
from app.config import settings
from app.database import get_db
from app import models, crud, services
from app.permissions import audit, require_admin, require_operator, require_viewer
from app.roster_import import TEMPLATE_CSV, parse_roster_csv, MAX_IMPORT_BYTES
from app.schemas import (StudentResponse as StudentSchema, StudentCreate as StudentCreateSchema, PersonType)

router = APIRouter()


def get_live_student(db: Session, student_id: int) -> models.Student:
    """Student that exists and has not been (soft) deleted, else 404"""
    student = db.query(models.Student).filter(
        models.Student.id == student_id, models.Student.deleted_at.is_(None)
    ).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.get("/", response_model=List[StudentSchema])
async def read_students(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=5000),
                        include_deleted: bool = False,
                        q: Optional[str] = Query(None, max_length=100, description="Search name or ID"),
                        class_name: Optional[str] = None,
                        person_type: Optional[PersonType] = None,
                        enrolled: Optional[bool] = Query(None, description="Only people with (true) / without (false) an active face"),
                        db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    query = db.query(models.Student).options(selectinload(models.Student.face_embeddings))
    if not include_deleted:
        query = query.filter(models.Student.deleted_at.is_(None))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(models.Student.name.ilike(like), models.Student.student_id.ilike(like)))
    if class_name:
        query = query.filter(models.Student.class_name == class_name)
    if person_type is not None:
        query = query.filter(models.Student.person_type == person_type.value)
    students = query.order_by(models.Student.id).all()
    if enrolled is not None:
        students = [s for s in students if (s.enrolled_faces > 0) == enrolled]
    return students[skip:skip + limit]


@router.get("/classes", response_model=List[str])
async def read_class_names(db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    """Distinct class groups of current students (for filters and timetable forms)"""
    rows = db.query(models.Student.class_name).filter(
        models.Student.deleted_at.is_(None), models.Student.class_name.isnot(None), models.Student.class_name != ""
    ).distinct().order_by(models.Student.class_name).all()
    return [r[0] for r in rows]


@router.get("/import/template", response_class=Response)
async def roster_template(current_user=Depends(require_viewer)):
    return Response(content=TEMPLATE_CSV, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="attendai_roster_template.csv"'})


@router.post("/import")
async def import_roster(file: UploadFile = File(...), dry_run: bool = Query(True),
                        db: Session = Depends(get_db), current_user=Depends(require_operator)):
    """Validate (dry_run=true, the default) or import a roster CSV. The import is
    all-or-nothing: nothing is written unless every row is valid."""
    contents = await file.read(MAX_IMPORT_BYTES + 1)
    if len(contents) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (maximum {MAX_IMPORT_BYTES // 1024} KB)")
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a .csv file (see the template)")

    existing_ids = {sid for (sid,) in db.query(models.Student.student_id).all()}
    report = parse_roster_csv(contents, existing_ids)
    result = {"dry_run": dry_run, "total_rows": report.total_rows, "valid_rows": len(report.rows),
              "errors": [e.__dict__ for e in report.errors], "created": 0}
    if dry_run or report.errors:
        return result

    for row in report.rows:
        db.add(models.Student(**row))
    audit(db, current_user, "students.import", "student", None,
          {"created": len(report.rows), "filename": file.filename})
    db.commit()
    result["created"] = len(report.rows)
    return result


@router.post("/", response_model=StudentSchema)
async def create_student(student: StudentCreateSchema, db: Session = Depends(get_db), current_user=Depends(require_operator)):
    # Check if student_id already exists (deleted students keep their ID for history)
    existing_student = db.query(models.Student).filter(models.Student.student_id == student.student_id).first()
    if existing_student:
        if existing_student.deleted_at is not None:
            raise HTTPException(status_code=400, detail="Student ID already exists (belongs to a deleted student)")
        raise HTTPException(status_code=400, detail="Student ID already exists")

    db_student = models.Student(**student.model_dump(mode="json"))
    db.add(db_student)
    db.flush()
    audit(db, current_user, "student.create", "student", db_student.id,
          {"student_id": db_student.student_id, "person_type": db_student.person_type})
    db.commit()
    db.refresh(db_student)
    return db_student


@router.get("/{student_id}", response_model=StudentSchema)
async def read_student(student_id: int, db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    return get_live_student(db, student_id)


@router.put("/{student_id}", response_model=StudentSchema)
async def update_student(student_id: int, student: StudentCreateSchema, db: Session = Depends(get_db), current_user=Depends(require_operator)):
    db_student = get_live_student(db, student_id)

    update_data = student.model_dump(exclude_unset=True, mode="json")
    new_identifier = update_data.get("student_id")
    if new_identifier is not None and new_identifier != db_student.student_id:
        clash = db.query(models.Student).filter(models.Student.student_id == new_identifier,
                                                models.Student.id != db_student.id).first()
        if clash is not None:
            raise HTTPException(status_code=400, detail="Student ID already exists")
    changed = {field: value for field, value in update_data.items() if getattr(db_student, field) != value}
    for field, value in update_data.items():
        setattr(db_student, field, value)
    if changed:
        audit(db, current_user, "student.update", "student", db_student.id, {"changed": sorted(changed)})

    db.commit()
    db.refresh(db_student)
    # is_active may have changed: running camera workers reload known faces
    services.notify_known_faces_changed()
    return db_student


@router.delete("/{student_id}")
async def delete_student(student_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """Soft delete: the student row and its attendance history are kept;
    the student is hidden from lists and no longer recognised."""
    student = get_live_student(db, student_id)
    audit(db, current_user, "student.delete", "student", student.id, {"student_id": student.student_id})
    crud.delete_student(db, student_id)
    services.notify_known_faces_changed()
    return {"message": "Student deleted successfully"}
