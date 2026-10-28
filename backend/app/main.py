
import os

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings
from app import database
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_VERSION = "1.0.0"

# Create FastAPI app
app = FastAPI(
    title="AttendAI API",
    description="Intelligent AI Video-Based Attendance & Classroom Analytics System (on-premise)",
    version=APP_VERSION,
    debug=settings.DEBUG
)

# Configure CORS (CORS_ORIGINS setting; the React frontend by default)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith(("/auth", "/students", "/attendance", "/face-enrollment", "/reports", "/users", "/portal", "/staff")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_without_secrets(request: Request, exc: RequestValidationError):
    """Same 422 body as FastAPI's default, except a rejected password is never echoed back
    (pydantic puts the submitted value in "input")."""
    def holds_password(value):
        # Whole-body and missing-field errors carry the entire submitted object as "input"
        return isinstance(value, dict) and any("password" in str(key).lower() or holds_password(item)
                                               for key, item in value.items())

    errors = []
    for error in exc.errors():
        if any("password" in str(part).lower() for part in error.get("loc", ())) or holds_password(error.get("input")):
            error = {key: value for key, value in error.items() if key != "input"}
        errors.append(error)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


@app.on_event("startup")
async def on_startup():
    from app.routers.video import close_stale_camera_runs
    schema = database.check_schema_version()
    if schema["state"] == "outdated":
        logger.error(f"Database schema is {schema['current']}, this code needs {schema['expected']}. Back up "
                     f"the database and run `alembic upgrade head` (see README). Most endpoints will fail until then.")
        return
    db = database.SessionLocal()
    try:
        closed = close_stale_camera_runs(db)
        if closed:
            logger.info(f"Closed {closed} camera run(s) left open by the previous server process")
    except Exception as e:
        db.rollback()
        logger.warning(f"Could not close stale camera runs: {type(e).__name__}")
    finally:
        db.close()


@app.on_event("shutdown")
async def on_shutdown():
    from app.routers.video import stop_all_sessions
    stop_all_sessions("server shutdown")


@app.get("/")
async def root():
    return {"message": "AttendAI API is running"}

@app.get("/health")
async def health_check():
    """
    Health check endpoint that verifies actual system components.
    Does NOT claim AI/camera health. Returns 503 if the database is unreachable
    or its schema is older than this code expects.
    """
    from app.services import resolve_model_path
    db_ok, db_detail = database.check_database_connection()
    schema = database.check_schema_version() if db_ok else {"state": "unknown"}
    model_path = resolve_model_path(settings.YOLO_MODEL_PATH)
    schema_ok = schema["state"] in ("up_to_date", "unversioned")
    health_status = {
        "status": "healthy" if db_ok and schema_ok else "unhealthy",
        "service": "attendai-api",
        "version": APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "checks": {
            "api": "ok",
            "database": "ok" if db_ok else "error",
            "schema": schema["state"],
            "configuration": "loaded",
            # Needed only to start cameras; reported so setup problems are visible early
            "person_detection_model": "ok" if os.path.isfile(model_path) else "missing",
        }
    }

    if not db_ok:
        logger.error(f"Health check: database unreachable: {db_detail}")
        health_status["errors"] = {"database": db_detail}
        return JSONResponse(status_code=503, content=health_status)
    if not schema_ok:
        health_status["errors"] = {"schema": f"Database schema {schema.get('current')} does not match the "
                                             f"expected {schema.get('expected')}; run `alembic upgrade head` "
                                             f"after backing up the database"}
        return JSONResponse(status_code=503, content=health_status)

    return health_status

# Import and include routers
from app.routers import (students, cameras, attendance, dashboard, auth, enrollment, video, face_enrollment,
                         users, timetable, reports, student_portal, staff_portal)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(students.router, prefix="/students", tags=["students"])
app.include_router(cameras.router, prefix="/cameras", tags=["cameras"])
app.include_router(attendance.router, prefix="/attendance", tags=["attendance"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(enrollment.router, prefix="/enrollment", tags=["enrollment"])
app.include_router(video.router, prefix="/video", tags=["video"])
app.include_router(face_enrollment.router, prefix="/face-enrollment", tags=["face-enrollment"])
app.include_router(timetable.router, prefix="/timetable", tags=["timetable"])
app.include_router(reports.router, prefix="/reports", tags=["reports"])
app.include_router(student_portal.router, prefix="/portal", tags=["student portal"])
app.include_router(staff_portal.router, prefix="/staff", tags=["staff portal"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)