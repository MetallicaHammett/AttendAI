"""
Account management (admin only) and the audit log.
"""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.database import get_db
from app.permissions import audit, require_admin
from app.security import get_password_hash

router = APIRouter()


def _user_response(user: models.User) -> schemas.UserResponse:
    return schemas.UserResponse(id=user.id, email=user.email, username=user.username, is_active=user.is_active,
                                role=user.role or "viewer", student_db_id=user.student_db_id,
                                created_at=user.created_at, updated_at=user.updated_at)


@router.get("/", response_model=List[schemas.UserResponse])
async def list_users(db: Session = Depends(get_db), current_user=Depends(require_admin)):
    return [_user_response(u) for u in db.query(models.User).order_by(models.User.id).all()]


@router.post("/", response_model=schemas.UserResponse, status_code=201)
async def create_user(account: schemas.UserAdminCreate, db: Session = Depends(get_db),
                      current_user=Depends(require_admin)):
    """Admin-created account with the chosen role. Works whether or not self-registration is open."""
    conflict = crud.find_conflicting_user(db, username=account.username, email=account.email)
    if conflict == "email":
        raise HTTPException(status_code=400, detail="Email already registered")
    if conflict == "username":
        raise HTTPException(status_code=400, detail="Username already taken")
    student = None
    if account.student_db_id is not None:
        student = db.query(models.Student).filter(models.Student.id == account.student_db_id).first()
        if student is None or student.deleted_at is not None or student.person_type != "student":
            raise HTTPException(status_code=400, detail="Choose an existing (not deleted) student record")
        if db.query(models.User).filter(models.User.student_db_id == student.id).first():
            raise HTTPException(status_code=409, detail=f"Student {student.student_id} already has an account")
    user = models.User(username=account.username, email=account.email, role=account.role.value, is_active=True,
                       student_db_id=account.student_db_id, hashed_password=get_password_hash(account.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:  # a simultaneous request got there first; the unique indexes are the final check
        db.rollback()
        raise HTTPException(status_code=400, detail="Username, email or student already has an account")
    # The password and its hash are never written to the audit log
    details = {"username": user.username, "role": user.role}
    if student is not None:
        details["student_id"] = student.student_id
    audit(db, current_user, "user.create", "user", user.id, details)
    db.commit()
    db.refresh(user)
    return _user_response(user)


@router.patch("/{user_id}", response_model=schemas.UserResponse)
async def update_user(user_id: int, changes: schemas.UserAdminUpdate, db: Session = Depends(get_db),
                      current_user=Depends(require_admin)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    # An admin cannot lock themself out (demote or deactivate their own account)
    if user.id == current_user.id and (
            (changes.role is not None and changes.role.value != "admin") or changes.is_active is False):
        raise HTTPException(status_code=400, detail="You cannot remove your own admin role or deactivate your own account")
    # A student account stays a student account (it is tied to one student record); it can
    # be deactivated or reactivated. Staff roles cannot become "student" (schema: StaffRole).
    if user.role == models.STUDENT_ROLE and changes.role is not None:
        raise HTTPException(status_code=400, detail="A student account cannot be given a staff role; create a separate staff account")

    before = {"role": user.role, "is_active": user.is_active}
    if changes.role is not None:
        user.role = changes.role.value
    if changes.is_active is not None:
        user.is_active = changes.is_active
    after = {"role": user.role, "is_active": user.is_active}
    # Never leave the system without an active admin (e.g. two admins demoting each other)
    if before["role"] == "admin" and before["is_active"] and not (after["role"] == "admin" and after["is_active"]) \
            and crud.count_other_active_admins(db, user.id) == 0:
        db.rollback()
        raise HTTPException(status_code=409, detail="This is the last active admin account; promote another admin first")
    audit(db, current_user, "user.update", "user", user.id, {"username": user.username, "before": before, "after": after})
    db.commit()
    db.refresh(user)
    return _user_response(user)


class AuditEntry(BaseModel):
    id: int
    created_at: Optional[schemas.UTCDateTime] = None
    username: Optional[str] = None
    action: str
    entity_type: str
    entity_id: Optional[str] = None
    details: Optional[dict] = None


@router.get("/audit-log", response_model=List[AuditEntry], include_in_schema=True)
async def read_audit_log(limit: int = Query(100, ge=1, le=1000), entity_type: Optional[str] = None,
                         db: Session = Depends(get_db), current_user=Depends(require_admin)):
    query = db.query(models.AuditLog)
    if entity_type:
        query = query.filter(models.AuditLog.entity_type == entity_type)
    rows = query.order_by(desc(models.AuditLog.id)).limit(limit).all()
    return [AuditEntry(id=r.id, created_at=r.created_at, username=r.username, action=r.action,
                       entity_type=r.entity_type, entity_id=r.entity_id,
                       details=json.loads(r.details) if r.details else None) for r in rows]
