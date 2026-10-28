"""
Role-based access control and the audit log.

Roles (lowest to highest privilege):
  viewer   - read-only: dashboards, attendance, reports, live view
  operator - day-to-day operation: students/staff, face enrollment, roster import,
             starting/stopping/testing cameras, attendance corrections and overrides
  admin    - configuration: cameras, classrooms, timetable, holidays, user accounts,
             deleting attendance records and face data

A "student" account is outside this ranking: has_role() is False for it at every level,
so it can use only the endpoints that explicitly require_student (its own attendance).
A "professor" account is outside it too: it can use only the staff portal
(require_professor_or_admin), and there only the timetable periods assigned to it.

Every check is enforced server-side by the dependencies below; the frontend only hides
controls the user cannot use.
"""
import json
import logging
from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models
from app.routers.auth import get_current_user

logger = logging.getLogger(__name__)

ROLE_RANK = {role: rank for rank, role in enumerate(models.USER_ROLES)}


def role_of(user: Any) -> Optional[str]:
    """Role of the authenticated user (schema object or, in tests, a dict)"""
    if isinstance(user, dict):
        return user.get("role")
    return getattr(user, "role", None)


def has_role(user: Any, minimum: str) -> bool:
    role = role_of(user)
    return role in ROLE_RANK and ROLE_RANK[role] >= ROLE_RANK[minimum]


def require_role(minimum: str):
    """Dependency: the current user must have at least `minimum` role (403 otherwise)"""
    if minimum not in ROLE_RANK:
        raise ValueError(f"Unknown role {minimum!r}")

    async def dependency(current_user=Depends(get_current_user)):
        if not has_role(current_user, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires the '{minimum}' role (you are '{role_of(current_user) or 'unknown'}')",
            )
        return current_user

    return dependency


async def require_student(current_user=Depends(get_current_user)):
    """Dependency: a student account (staff use the staff endpoints instead)"""
    if role_of(current_user) != models.STUDENT_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only student accounts have a personal attendance page")
    return current_user


async def require_professor_or_admin(current_user=Depends(get_current_user)):
    """Dependency: the staff portal. A professor reaches only the periods assigned to them
    (checked per period by the staff portal); an admin oversees every register."""
    if role_of(current_user) not in (models.PROFESSOR_ROLE, "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="The staff portal is for professor accounts and administrators")
    return current_user


require_viewer = require_role("viewer")
require_operator = require_role("operator")
require_admin = require_role("admin")


def audit(db: Session, user: Any, action: str, entity_type: str, entity_id: Any = None,
          details: Optional[dict] = None, commit: bool = False):
    """Append an audit record (part of the caller's transaction unless commit=True).
    details must never contain passwords, tokens, camera credentials or face data."""
    if isinstance(user, dict):
        user_id, username = user.get("id"), user.get("username")
    else:
        user_id, username = getattr(user, "id", None), getattr(user, "username", None)
    entry = models.AuditLog(
        user_id=user_id, username=username, action=action, entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        details=json.dumps(details, default=str, sort_keys=True) if details else None,
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
