"""
Pydantic schemas for AttendAI API requests and responses.
"""
import re

from pydantic import AfterValidator, BaseModel, Field, model_validator
from typing import Annotated, Optional, List
from datetime import datetime, timezone
from enum import Enum


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Stored timestamps are naive UTC (SQLite CURRENT_TIMESTAMP); make them
    timezone-aware so every API response serialises them with a UTC offset ("Z")."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# Use for every datetime field in a response schema
UTCDateTime = Annotated[datetime, AfterValidator(ensure_utc)]


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class SourceType(str, Enum):
    WEBCAM = "webcam"
    VIDEO = "video"
    RTSP = "rtsp"


class PersonType(str, Enum):
    STUDENT = "student"
    STAFF = "staff"


def _strip_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


# Leading/trailing whitespace removed; blank values rejected
StrippedStr = Annotated[str, AfterValidator(_strip_required)]


# Student Schemas (staff use the same records with person_type="staff")
class StudentBase(BaseModel):
    student_id: StrippedStr = Field(..., min_length=1, max_length=50, description="Unique student/staff identifier")
    name: StrippedStr = Field(..., min_length=1, max_length=100, description="Full name")
    class_name: Optional[str] = Field(None, max_length=100, description="Class group (matches timetable periods)")
    department: Optional[str] = Field(None, max_length=100, description="Department or faculty")
    is_active: bool = Field(True, description="Whether the person is active (inactive people are not recognised)")
    person_type: PersonType = Field(PersonType.STUDENT, description="student or staff")


class StudentCreate(StudentBase):
    pass


class StudentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    class_name: Optional[str] = Field(None, max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None
    person_type: Optional[PersonType] = None


class StudentResponse(StudentBase):
    id: int
    created_at: UTCDateTime
    updated_at: Optional[UTCDateTime] = None
    deleted_at: Optional[UTCDateTime] = None  # set when the student has been (soft) deleted
    enrolled_faces: int = 0  # active face embeddings; 0 = cannot be recognised yet

    class Config:
        from_attributes = True


# Face Embedding Schemas
class FaceEmbeddingBase(BaseModel):
    is_active: bool = Field(True, description="Whether the face embedding is active")


class FaceEmbeddingCreate(FaceEmbeddingBase):
    embedding_data: bytes = Field(..., description="Serialized face embedding data")


class FaceEmbeddingResponse(FaceEmbeddingBase):
    id: int
    student_id: int
    created_at: UTCDateTime
    updated_at: Optional[UTCDateTime] = None

    class Config:
        from_attributes = True


# Camera Schemas
class CameraType(str, Enum):
    IN = "IN"
    OUT = "OUT"


class CameraBase(BaseModel):
    name: StrippedStr = Field(..., min_length=1, max_length=100, description="Camera name")
    source: str = Field(..., min_length=1, max_length=255, description="Camera source (URL, device index, etc.)")
    source_type: SourceType = Field(..., description="Type of camera source")
    camera_type: CameraType = Field(..., description="Camera type: IN for entry, OUT for exit")
    is_active: bool = Field(True, description="Whether the camera is active")
    width: int = Field(640, gt=0, le=7680, description="Video width in pixels")
    height: int = Field(480, gt=0, le=4320, description="Video height in pixels")
    fps: int = Field(30, gt=0, le=120, description="Frames per second")
    description: Optional[str] = Field(None, max_length=1000, description="Camera description")
    classroom_id: Optional[int] = Field(None, description="Classroom this camera watches")


class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    source: Optional[str] = Field(None, min_length=1, max_length=255)
    source_type: Optional[SourceType] = None
    is_active: Optional[bool] = None
    width: Optional[int] = Field(None, gt=0)
    height: Optional[int] = Field(None, gt=0)
    fps: Optional[int] = Field(None, gt=0)
    description: Optional[str] = None


def _mask_source_field(value: str) -> str:
    from app.sources import mask_source
    return mask_source(value)


class CameraResponse(CameraBase):
    # Stream credentials are never returned (rtsp://***@host/...)
    source: Annotated[str, AfterValidator(_mask_source_field)]
    id: int
    created_at: UTCDateTime
    updated_at: Optional[UTCDateTime] = None
    deleted_at: Optional[UTCDateTime] = None  # set when the camera has been (soft) deleted

    class Config:
        from_attributes = True


# Attendance Event Schemas
class AttendanceEventBase(BaseModel):
    event_type: EventType = Field(..., description="Type of attendance event")
    confidence: int = Field(..., ge=0, le=100, description="Confidence percentage (0-100)")


class AttendanceEventCreate(AttendanceEventBase):
    student_id: int = Field(..., description="ID of the student")
    camera_id: Optional[int] = Field(None, description="ID of the camera (optional)")


class AttendanceEventResponse(AttendanceEventBase):
    id: int
    student_id: int
    camera_id: Optional[int] = None
    timestamp: UTCDateTime
    created_at: UTCDateTime
    updated_at: Optional[UTCDateTime] = None
    source: str = "camera"  # camera or manual
    note: Optional[str] = None

    class Config:
        from_attributes = True


# Response models with related data
class StudentWithEmbeddings(StudentResponse):
    face_embeddings: List[FaceEmbeddingResponse] = []


class CameraWithEvents(CameraResponse):
    attendance_events: List[AttendanceEventResponse] = []


class StudentWithEvents(StudentResponse):
    attendance_events: List[AttendanceEventResponse] = []


# Dashboard/Statistics Schemas
class DashboardStats(BaseModel):
    total_students: int
    active_students: int
    total_cameras: int
    active_cameras: int
    in_cameras: int
    out_cameras: int
    attendance_today: int
    today_entries: int
    today_exits: int
    estimated_present: int
    attendance_this_week: int
    # Distinct registered students (not staff) with an ENTRY today (attendance_today counts events)
    present_today: Optional[int] = None
    # Roster (active, not deleted)
    student_count: Optional[int] = None
    staff_count: Optional[int] = None
    enrolled_people: Optional[int] = None  # people with at least one active face
    # Inside now, from stored events: latest event today is an ENTRY
    # (estimated_present is the same number, kept for compatibility)
    known_inside_count: Optional[int] = None
    students_inside: Optional[int] = None
    staff_inside: Optional[int] = None
    # Unregistered people: in-memory estimate from face matching, since unknown_tracking_since
    unknown_inside_count: Optional[int] = None
    unknown_entries_today: Optional[int] = None
    unknown_tracking_since: Optional[float] = None  # epoch seconds; None = no camera has run since start
    unknown_tracking_active: Optional[bool] = None
    cameras_processing: Optional[int] = None
    cameras_unhealthy: Optional[int] = None
    classroom_occupancy: Optional[List[dict]] = None
    # Real-time processing stats (when cameras are active)
    current_inside_count: Optional[int] = None
    total_known_recognitions: Optional[int] = None
    total_unknown_seen: Optional[int] = None

    class Config:
        from_attributes = True


class AttendanceRecord(BaseModel):
    id: int
    student_name: str
    student_id: str  # Institution student ID (students.student_id)
    event_type: EventType
    timestamp: UTCDateTime  # Timezone-aware UTC
    confidence: int
    camera_name: Optional[str] = None
    student_db_id: Optional[int] = None  # students.id
    camera_id: Optional[int] = None
    source: str = "camera"
    person_type: Optional[str] = None

    class Config:
        from_attributes = True


# Authentication Schemas
class UserBase(BaseModel):
    email: str = Field(..., max_length=255, description="User email address")
    username: str = Field(..., max_length=50, description="Username")


USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{3,50}")
EMAIL_PATTERN = re.compile(r"[^@\s\[\]()<>,;\"']+@[^@\s\[\]()<>,;\"']+\.[^@\s\[\]()<>,;\"'.]+")


def _valid_new_username(value: str) -> str:
    value = value.strip()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ValueError("must be 3-50 characters: letters, digits, '.', '_' or '-'")
    return value


def _valid_new_email(value: str) -> str:
    value = value.strip()
    if not EMAIL_PATTERN.fullmatch(value):
        raise ValueError("must be a plain email address such as name@example.edu")
    return value


class UserCreate(UserBase):
    """New accounts only. Existing records (UserResponse) are not re-validated, so legacy
    values never make listings fail."""
    username: Annotated[str, AfterValidator(_valid_new_username)] = Field(..., max_length=50)
    email: Annotated[str, AfterValidator(_valid_new_email)] = Field(..., max_length=255)
    # bcrypt only uses the first 72 bytes, so longer passwords are rejected instead of truncated
    password: str = Field(..., min_length=8, max_length=72, description="Password (8-72 characters)")


class UserLogin(BaseModel):
    username: str = Field(..., description="Username")
    password: str = Field(..., description="Password")


class Token(BaseModel):
    access_token: str
    token_type: str

    class Config:
        from_attributes = True


class TokenData(BaseModel):
    username: Optional[str] = None


class UserRole(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"
    STUDENT = "student"
    PROFESSOR = "professor"


class StaffRole(str, Enum):
    """Roles an admin may assign by changing a role. Student accounts are created linked to
    one student record and are never converted to or from staff accounts."""
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"
    PROFESSOR = "professor"


class UserResponse(UserBase):
    id: int
    is_active: bool
    role: UserRole = UserRole.VIEWER
    student_db_id: Optional[int] = None  # student accounts: the linked students.id
    created_at: UTCDateTime
    updated_at: Optional[UTCDateTime] = None

    class Config:
        from_attributes = True


class UserAdminCreate(UserCreate):
    """An account created by an admin, who chooses the role (self-registration is always
    viewer). A student account must name the one student record it may see; a staff
    account must not name one."""
    role: UserRole = UserRole.VIEWER
    student_db_id: Optional[int] = Field(None, gt=0, description="students.id; required for (and only for) role 'student'")

    @model_validator(mode="after")
    def link_matches_role(self):
        if self.role == UserRole.STUDENT and self.student_db_id is None:
            raise ValueError("a student account needs the student record it belongs to (student_db_id)")
        if self.role != UserRole.STUDENT and self.student_db_id is not None:
            raise ValueError("only student accounts are linked to a student record")
        return self


class UserAdminUpdate(BaseModel):
    """Changes an admin may make to another account"""
    role: Optional[StaffRole] = None
    is_active: Optional[bool] = None