"""
SQLAlchemy models for AttendAI application.
"""
from sqlalchemy import (Column, Integer, String, DateTime, Date, Time, Boolean, Text, ForeignKey, LargeBinary,
                        UniqueConstraint)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base

# Staff roles, lowest to highest privilege. See app/permissions.py.
USER_ROLES = ("viewer", "operator", "admin")
# A student account sees only its own attendance. It is deliberately outside the staff
# ranking, so every staff permission check refuses it.
STUDENT_ROLE = "student"
# A professor sees only the timetable periods assigned to them and their class registers.
# Also outside the staff ranking: no staff endpoint (cameras, users, reports) accepts it.
PROFESSOR_ROLE = "professor"
ACCOUNT_ROLES = USER_ROLES + (STUDENT_ROLE, PROFESSOR_ROLE)
PERSON_TYPES = ("student", "staff")


# Base model with common fields
class BaseModel(Base):
    __abstract__ = True

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

# User model for authentication
class User(BaseModel):
    __tablename__ = "users"

    email = Column(String(255), unique=True, nullable=False, index=True)  # Using email as login identifier
    username = Column(String(50), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    # viewer: read-only; operator: day-to-day operation (students, enrollment, cameras
    # start/stop, corrections); admin: configuration and user management
    role = Column(String(20), nullable=False, default="viewer", server_default="viewer")
    # Student accounts only: the student record whose attendance this account may see
    student_db_id = Column(Integer, ForeignKey("students.id"), nullable=True, unique=True)

# Student model. Also holds staff (person_type="staff"): both are enrolled and
# recognised the same way; only students count towards class attendance.
class Student(BaseModel):
    __tablename__ = "students"

    student_id = Column(String(50), unique=True, nullable=False, index=True)  # Institution/student ID
    name = Column(String(100), nullable=False)
    class_name = Column(String(100), nullable=True)  # class group; matches TimetablePeriod.class_name
    department = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    person_type = Column(String(20), nullable=False, default="student", server_default="student")
    # Soft delete: set instead of deleting the row, so attendance history is kept
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    face_embeddings = relationship("FaceEmbedding", back_populates="student", cascade="all, delete-orphan")
    # No delete cascade and passive_deletes="all": the ORM never deletes or nulls
    # attendance/enrollment rows; with SQLite foreign keys on (app.database) a hard
    # delete of a student that has history is rejected by the database.
    attendance_events = relationship("AttendanceEvent", back_populates="student",
                                     cascade="save-update, merge", passive_deletes="all")
    enrollments = relationship("Enrollment", back_populates="student",
                               cascade="save-update, merge", passive_deletes="all")

    @property
    def enrolled_faces(self) -> int:
        """Active face embeddings usable by the active face model (0 = cannot be recognised:
        not enrolled, or enrolled only with another model and needs a new photo)"""
        from app import face_models
        model = face_models.active_model()
        return sum(1 for e in self.face_embeddings if e.is_active and face_models.model_of(e.embedding_data) == model)

# FaceEmbedding model
class FaceEmbedding(BaseModel):
    __tablename__ = "face_embeddings"

    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    embedding_data = Column(LargeBinary, nullable=False)  # Stores serialized face embedding
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    student = relationship("Student", back_populates="face_embeddings")

# Physical room with entry/exit cameras
class Classroom(BaseModel):
    __tablename__ = "classrooms"

    name = Column(String(100), unique=True, nullable=False)
    building = Column(String(100), nullable=True)
    capacity = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    cameras = relationship("Camera", back_populates="classroom")
    periods = relationship("TimetablePeriod", back_populates="classroom")

# Camera model
class Camera(BaseModel):
    __tablename__ = "cameras"

    name = Column(String(100), nullable=False)
    source = Column(String(255), nullable=False)  # RTSP URL, device index, or file path
    source_type = Column(String(20), nullable=False)  # webcam, video, rtsp
    camera_type = Column(String(10), nullable=False)  # IN or OUT
    is_active = Column(Boolean, default=True, nullable=False)
    width = Column(Integer, default=640)
    height = Column(Integer, default=480)
    fps = Column(Integer, default=30)
    description = Column(Text, nullable=True)
    classroom_id = Column(Integer, ForeignKey("classrooms.id"), nullable=True, index=True)
    # Soft delete: set instead of deleting the row, so attendance history is kept
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    # No delete cascade and passive_deletes="all": the ORM never nulls camera_id on
    # history; with SQLite foreign keys on, hard-deleting a camera with history fails.
    attendance_events = relationship("AttendanceEvent", back_populates="camera",
                                     cascade="save-update, merge", passive_deletes="all")
    classroom = relationship("Classroom", back_populates="cameras")

# AttendanceEvent model
class AttendanceEvent(BaseModel):
    __tablename__ = "attendance_events"

    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=True)  # Nullable for cases where camera isn't applicable
    event_type = Column(String(10), nullable=False)  # ENTRY, EXIT
    # Written by the DB (SQLite CURRENT_TIMESTAMP = naive UTC)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    confidence = Column(Integer, nullable=False)  # 0-100 match score (100 for manual events)
    # "camera" (recorded by a camera worker) or "manual" (operator correction, audited)
    source = Column(String(20), nullable=False, default="camera", server_default="camera")
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)

    # Relationships
    student = relationship("Student", back_populates="attendance_events")
    camera = relationship("Camera", back_populates="attendance_events")

# Course enrollment (student <-> course for a semester)
class Enrollment(BaseModel):
    __tablename__ = "enrollments"

    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    course_code = Column(String(50), nullable=False)
    course_name = Column(String(200), nullable=False)
    semester = Column(String(50), nullable=False)
    section = Column(String(20), nullable=True)
    enrolled_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    student = relationship("Student", back_populates="enrollments")

# Weekly recurring class period for one class group in one classroom
class TimetablePeriod(BaseModel):
    __tablename__ = "timetable_periods"

    classroom_id = Column(Integer, ForeignKey("classrooms.id"), nullable=False, index=True)
    class_name = Column(String(100), nullable=False, index=True)  # roster = students with this class_name
    course_code = Column(String(50), nullable=True)
    course_name = Column(String(200), nullable=False)
    day_of_week = Column(Integer, nullable=False)  # 0 = Monday ... 6 = Sunday
    start_time = Column(Time, nullable=False)  # local time
    end_time = Column(Time, nullable=False)
    grace_minutes = Column(Integer, nullable=True)  # None -> ATTENDANCE_DEFAULT_GRACE_MINUTES
    is_active = Column(Boolean, default=True, nullable=False)
    # The professor who teaches this period (a "professor" account); NULL = not assigned
    professor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    classroom = relationship("Classroom", back_populates="periods")
    professor = relationship("User")

# A professor's submitted register for one period on one date. The per-student decisions
# are AttendanceOverride rows; this row records who submitted the register and when, and
# its version number rejects a save based on an out-of-date copy.
class AttendanceRegister(BaseModel):
    __tablename__ = "attendance_registers"
    __table_args__ = (UniqueConstraint("period_id", "date", name="uq_register_period_date"),)

    period_id = Column(Integer, ForeignKey("timetable_periods.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    version = Column(Integer, nullable=False, default=1)  # +1 on every save
    first_submitted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    first_submitted_at = Column(DateTime(timezone=True), nullable=False)
    submitted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  # latest save
    submitted_at = Column(DateTime(timezone=True), nullable=False)

# Day without classes (no attendance is computed)
class Holiday(BaseModel):
    __tablename__ = "holidays"

    date = Column(Date, nullable=False, unique=True)
    name = Column(String(200), nullable=False)

# Authorised manual decision for one student in one period on one date (audited)
class AttendanceOverride(BaseModel):
    __tablename__ = "attendance_overrides"
    __table_args__ = (UniqueConstraint("period_id", "date", "student_id", name="uq_override_period_date_student"),)

    period_id = Column(Integer, ForeignKey("timetable_periods.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False)  # PRESENT, LATE, ABSENT, EXCUSED
    reason = Column(Text, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

# Append-only record of privileged changes
class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(50), nullable=True)
    action = Column(String(50), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(50), nullable=True)
    details = Column(Text, nullable=True)  # JSON; never contains face data or passwords

# One period during which a camera worker was running. Used to tell "absent" apart
# from "no camera was watching" when computing period attendance.
class CameraRun(Base):
    __tablename__ = "camera_runs"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)  # naive UTC
    stopped_at = Column(DateTime(timezone=True), nullable=True)  # NULL = still running (or crashed)
    # Updated periodically by the worker; after a crash, coverage ends here
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    feed_kind = Column(String(20), nullable=True)  # live / recorded / synthetic
    stop_reason = Column(String(100), nullable=True)
