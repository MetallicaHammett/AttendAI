import logging
import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import crud, models, rtsp_check
from app.config import settings
from app.database import get_db
from app.permissions import audit, require_admin, require_operator, require_viewer
from app.schemas import CameraResponse as CameraSchema, CameraCreate as CameraCreateSchema
from app.sources import mask_source, restore_credentials, validate_source

logger = logging.getLogger(__name__)

router = APIRouter()

CONNECTION_TEST_TIMEOUT_SECONDS = 8.0


def get_live_camera(db: Session, camera_id: int) -> models.Camera:
    """Camera that exists and has not been (soft) deleted, else 404"""
    camera = db.query(models.Camera).filter(
        models.Camera.id == camera_id, models.Camera.deleted_at.is_(None)
    ).first()
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


def _validated(db: Session, camera: CameraCreateSchema, stored_source: str = None) -> dict:
    data = camera.model_dump(mode="json")
    source = data["source"]
    if stored_source is not None:
        try:
            source = restore_credentials(source, stored_source)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    error, normalised = validate_source(data["source_type"], source, settings.VIDEO_FILES_DIR)
    if error:
        raise HTTPException(status_code=400, detail=error)
    data["source"] = normalised
    if data.get("classroom_id") is not None:
        classroom = db.query(models.Classroom).filter(models.Classroom.id == data["classroom_id"],
                                                      models.Classroom.deleted_at.is_(None)).first()
        if classroom is None:
            raise HTTPException(status_code=400, detail="Classroom not found")
    return data


@router.get("/", response_model=List[CameraSchema])
async def read_cameras(skip: int = 0, limit: int = 100, include_deleted: bool = False, db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    cameras = crud.get_cameras(db, skip=skip, limit=limit, include_deleted=include_deleted)
    return cameras


@router.post("/", response_model=CameraSchema)
async def create_camera(camera: CameraCreateSchema, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    db_camera = models.Camera(**_validated(db, camera))
    db.add(db_camera)
    db.flush()
    audit(db, current_user, "camera.create", "camera", db_camera.id,
          {"name": db_camera.name, "source_type": db_camera.source_type, "camera_type": db_camera.camera_type,
           "source": mask_source(db_camera.source)})
    db.commit()
    db.refresh(db_camera)
    return db_camera


@router.get("/{camera_id}", response_model=CameraSchema)
async def read_camera(camera_id: int, db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    return get_live_camera(db, camera_id)


@router.put("/{camera_id}", response_model=CameraSchema)
async def update_camera(camera_id: int, camera: CameraCreateSchema, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    db_camera = get_live_camera(db, camera_id)

    from app.routers.video import active_sessions
    update_data = _validated(db, camera, stored_source=db_camera.source)
    if camera_id in active_sessions and any(
            update_data[f] != getattr(db_camera, f) for f in ("source", "source_type", "camera_type")):
        raise HTTPException(status_code=409, detail="Stop video processing before changing this camera's source or role")

    changed = sorted(f for f, v in update_data.items() if getattr(db_camera, f) != v)
    for field, value in update_data.items():
        setattr(db_camera, field, value)
    if changed:
        audit(db, current_user, "camera.update", "camera", db_camera.id, {"changed": changed})

    db.commit()
    db.refresh(db_camera)
    return db_camera


@router.delete("/{camera_id}")
async def delete_camera(camera_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    """Soft delete: the camera row and its attendance history are kept."""
    camera = get_live_camera(db, camera_id)

    from app.routers.video import active_sessions
    if camera_id in active_sessions:
        raise HTTPException(status_code=409, detail="Stop video processing for this camera before deleting it")

    audit(db, current_user, "camera.delete", "camera", camera_id, {"name": camera.name})
    crud.delete_camera(db, camera_id)
    return {"message": "Camera deleted successfully"}


@router.post("/{camera_id}/toggle")
async def toggle_camera(camera_id: int, db: Session = Depends(get_db), current_user=Depends(require_admin)):
    db_camera = get_live_camera(db, camera_id)

    from app.routers.video import active_sessions
    if db_camera.is_active and camera_id in active_sessions:
        raise HTTPException(status_code=409, detail="Stop video processing before deactivating this camera")

    db_camera.is_active = not db_camera.is_active
    audit(db, current_user, "camera.toggle", "camera", camera_id, {"is_active": db_camera.is_active})
    db.commit()
    db.refresh(db_camera)
    return {"message": f"Camera {'activated' if db_camera.is_active else 'deactivated'} successfully"}


def probe_source(source, width: int, height: int):
    """Open the source, read one frame, release it. Returns (ok, detail, frame_shape)."""
    import cv2
    capture_source = int(source) if isinstance(source, str) and source.isdigit() else source
    pre_checked = False
    if isinstance(capture_source, str) and capture_source.lower().startswith(("rtsp://", "rtsps://")):
        # Says why an RTSP stream fails (unreachable, login rejected, wrong channel ...) with at
        # most one login attempt, and skips OpenCV when the stream cannot open anyway
        result = rtsp_check.check(capture_source, timeout=settings.CAMERA_OPEN_TIMEOUT_SECONDS)
        if result.ok is False:
            return False, result.detail, None
        pre_checked = result.ok is True
    if isinstance(capture_source, str) and "://" in capture_source:
        # Bounded waits: an unreachable camera fails in seconds (RTSP uses TCP, see app.services)
        cap = cv2.VideoCapture(capture_source, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(settings.CAMERA_OPEN_TIMEOUT_SECONDS * 1000),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(settings.CAMERA_READ_TIMEOUT_SECONDS * 1000)])
    else:
        cap = cv2.VideoCapture(capture_source)
    try:
        if not cap.isOpened():
            if pre_checked:
                return False, ("The device accepted the login and has this stream, but it could not be decoded "
                               "(codec or transport). Try the sub-stream (channel ...02)."), None
            return False, "Could not open the source", None
        deadline = time.time() + CONNECTION_TEST_TIMEOUT_SECONDS
        while time.time() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None:
                return True, "Opened and read a frame", list(frame.shape)
            time.sleep(0.2)
        return False, "Opened, but no frame was received", None
    finally:
        cap.release()


@router.post("/{camera_id}/test")
async def test_camera_connection(camera_id: int,
                                 confirm_device: bool = Query(False, description="Required for webcam sources: "
                                                              "the test switches the device on briefly"),
                                 db: Session = Depends(get_db), current_user=Depends(require_operator)):
    """Check that a camera's source can be opened and delivers a frame. The source is
    released immediately; no frame is stored or returned, and nothing is processed."""
    camera = get_live_camera(db, camera_id)
    from app.routers.video import active_sessions, source_in_use

    if camera_id in active_sessions:
        stats = active_sessions[camera_id]["service"].get_stats()
        return {"camera_id": camera_id, "ok": stats.get("camera_healthy", False), "tested": False,
                "detail": "Camera is already being processed; showing live status instead of opening it again",
                "state": stats.get("state")}
    in_use_by = source_in_use(camera.source, exclude_camera_id=camera_id)
    if in_use_by is not None:
        raise HTTPException(status_code=409, detail=f"The same source is being processed by camera {in_use_by}; "
                                                    f"stop it first")
    if camera.source_type == "webcam" and not confirm_device:
        raise HTTPException(status_code=400, detail=f"Testing a webcam switches on device {camera.source} on the "
                                                    f"server. Confirm that this is the intended device "
                                                    f"(confirm_device=true).")
    if camera.source_type == "video":
        error, _ = validate_source("video", camera.source, settings.VIDEO_FILES_DIR)
        if error:
            return {"camera_id": camera_id, "ok": False, "tested": True, "detail": error}

    from starlette.concurrency import run_in_threadpool
    ok, detail, shape = await run_in_threadpool(probe_source, camera.source, camera.width, camera.height)
    audit(db, current_user, "camera.test", "camera", camera_id, {"ok": ok}, commit=True)
    return {"camera_id": camera_id, "ok": ok, "tested": True, "detail": detail, "frame_shape": shape,
            "source": mask_source(camera.source)}
