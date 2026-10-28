from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
from app.config import settings
from app.database import get_db, SessionLocal
from app import crud, face_models, models, rtsp_check
from app import services
from app.permissions import audit, require_operator, require_viewer
from app.sources import mask_source, resolve_video_source
from datetime import datetime, timedelta, timezone
import threading
import time
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Active video processing sessions of this process (one backend worker process; see README).
active_sessions: Dict[int, Dict[str, Any]] = {}
_sessions_lock = threading.Lock()

HEARTBEAT_SECONDS = 30.0


def utc_now_naive() -> datetime:
    """Current time as naive UTC, the format timestamps are stored in"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_unknown_tracker() -> services.UnknownOccupancyTracker:
    """The single UnknownOccupancyTracker shared by every camera worker (in memory)"""
    limits = face_models.thresholds()  # distances depend on the active face model
    return services.get_shared_unknown_tracker(
        same_person_distance=limits["unknown_same_person"],
        min_consecutive_frames=settings.ATTENDANCE_MIN_CONSECUTIVE_FRAMES,
        max_frame_gap=settings.ATTENDANCE_MAX_FRAME_GAP_SECONDS,
        exit_cooldown=settings.ATTENDANCE_EXIT_COOLDOWN_SECONDS,
        min_presence_seconds=settings.ATTENDANCE_MIN_PRESENCE_SECONDS,
        resolve_known_distance=limits["unknown_resolve_known"],
    )


def _normalised_source(source: Any) -> str:
    text = str(source).strip()
    return str(int(text)) if text.isdigit() else text


def source_in_use(source: Any, exclude_camera_id: Optional[int] = None) -> Optional[int]:
    """ID of a running camera that reads the same physical source, else None. Two camera
    records pointing at device 0 must never both open it."""
    wanted = _normalised_source(source)
    for camera_id, info in list(active_sessions.items()):
        if camera_id != exclude_camera_id and _normalised_source(info.get("source", "")) == wanted:
            return camera_id
    return None


def _close_run(run_id: Optional[int], reason: str):
    if run_id is None:
        return
    db = SessionLocal()
    try:
        run = db.query(models.CameraRun).filter(models.CameraRun.id == run_id).first()
        if run is not None and run.stopped_at is None:
            run.stopped_at = utc_now_naive()
            run.stop_reason = reason
            db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Could not close camera run {run_id}: {type(e).__name__}: {e}")
    finally:
        db.close()


def close_stale_camera_runs(db: Session) -> int:
    """At startup no worker is running, so any open run ended when the previous process
    stopped. Coverage for it ends at its last heartbeat."""
    stale = db.query(models.CameraRun).filter(models.CameraRun.stopped_at.is_(None)).all()
    for run in stale:
        run.stopped_at = run.last_heartbeat_at or run.started_at
        run.stop_reason = "server stopped"
    db.commit()
    return len(stale)


def _epoch_to_utc_naive(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


def track_coverage(db: Session, session_info: Dict[str, Any], service, camera_id: int,
                   now: Optional[float] = None) -> None:
    """Keep the camera's coverage records (camera_runs) equal to the time frames really
    arrived. Period attendance only calls a student absent when the classroom camera was
    covering, so an outage must never look like coverage:
      - frames fresh (state running, newest frame within CAMERA_STALE_AFTER_SECONDS):
        heartbeat the open record every HEARTBEAT_SECONDS, or open a new one after an outage
      - no fresh frames (disconnected, reconnecting, stalled, file ended): close the open
        record at the last frame actually received
      - nothing received yet: wait out the model start-up, then close it as never covering
    Called by the camera worker after every frame attempt."""
    now = time.time() if now is None else now
    last_frame = getattr(service, "last_frame_at", None) or None
    if last_frame is None and getattr(service, "use_test_source", False):
        last_frame = getattr(service, "latest_frame_timestamp", None) or None  # synthetic dev frames
    fresh = (getattr(service, "state", None) == "running" and last_frame is not None
             and now - last_frame <= settings.CAMERA_STALE_AFTER_SECONDS)
    run_id = session_info.get("run_id")
    try:
        if fresh:
            if run_id is None:
                run = models.CameraRun(camera_id=camera_id, started_at=_epoch_to_utc_naive(last_frame),
                                       last_heartbeat_at=utc_now_naive(), feed_kind=service.feed_kind)
                db.add(run)
                db.commit()
                session_info["run_id"], session_info["last_heartbeat"] = run.id, now
                logger.info(f"Camera {camera_id}: frames are arriving again; coverage resumed")
            elif now - session_info.get("last_heartbeat", 0) >= HEARTBEAT_SECONDS:
                run = db.query(models.CameraRun).filter(models.CameraRun.id == run_id).first()
                if run is not None:
                    run.last_heartbeat_at = utc_now_naive()
                    db.commit()
                session_info["last_heartbeat"] = now
            return
        if run_id is None:
            return
        started = getattr(service, "started_at", None) or now
        if last_frame is None and now - started < max(settings.CAMERA_STALE_AFTER_SECONDS, 30.0):
            return  # models still loading / first frame not yet decoded
        run = db.query(models.CameraRun).filter(models.CameraRun.id == run_id).first()
        if run is not None and run.stopped_at is None:
            run.stopped_at = _epoch_to_utc_naive(last_frame) if last_frame else run.started_at
            reason = getattr(service, "state", None) or "no frames"
            run.stop_reason = "feed ended" if reason == "ended" else f"feed lost ({reason})"
            db.commit()
            logger.warning(f"Camera {camera_id}: no fresh frames ({reason}); coverage paused until frames return")
        session_info["run_id"] = None
    except Exception as e:  # coverage bookkeeping must never stop the camera
        db.rollback()
        logger.error(f"Camera {camera_id}: could not update coverage: {type(e).__name__}")


def get_attendance_engine() -> services.AttendanceEngine:
    """The single AttendanceEngine shared by every camera worker"""
    return services.get_shared_attendance_engine(
        # Confidence scales differ per face model (dlib: 1 - L2 distance; SFace: cosine)
        confidence_threshold=face_models.thresholds()["min_confidence"],
        min_consecutive_frames=settings.ATTENDANCE_MIN_CONSECUTIVE_FRAMES,
        entry_cooldown=settings.ATTENDANCE_ENTRY_COOLDOWN_SECONDS,
        exit_cooldown=settings.ATTENDANCE_EXIT_COOLDOWN_SECONDS,
        min_presence_seconds=settings.ATTENDANCE_MIN_PRESENCE_SECONDS,
        max_frame_gap=settings.ATTENDANCE_MAX_FRAME_GAP_SECONDS,
    )


def seed_engine_from_db(engine: services.AttendanceEngine, db: Session):
    """Restore who is inside from the last 24h of persisted events (the engine
    itself only treats same-local-day entries as active)."""
    # attendance_events.timestamp is written by the DB (SQLite CURRENT_TIMESTAMP = naive UTC)
    since = utc_now_naive().replace(microsecond=0) - timedelta(hours=24)
    events = db.query(models.AttendanceEvent).filter(
        crud.timestamp_at_or_after(models.AttendanceEvent.timestamp, since)
    ).all()
    # Each event belongs to the zone (classroom) of the camera that recorded it
    camera_rooms = {c.id: c.classroom_id for c in db.query(models.Camera).all()}
    seeded = []
    for event in events:
        ts = event.timestamp
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        seeded.append((event.student_id, event.event_type, ts.timestamp(), camera_rooms.get(event.camera_id)))
    engine.seed_from_events(seeded)
    return len(seeded)


def make_event_recorder(db: Session, camera_id: int, session_info: Dict[str, Any]):
    """Persist ENTRY/EXIT decisions with the worker's own session. Raising makes the
    engine roll the decision back, so state and database never disagree."""
    from app.schemas import AttendanceEventCreate

    def record_event(decision: services.AttendanceDecision):
        if decision.student_id is None:
            raise ValueError("Attempted to record attendance with null student ID")
        # Verify student and camera exist and have not been (soft) deleted
        if crud.get_live_student(db, decision.student_id) is None:
            raise ValueError(f"Attempted to record attendance for non-existent or deleted student ID: {decision.student_id}")
        if camera_id is not None and crud.get_live_camera(db, camera_id) is None:
            raise ValueError(f"Attempted to record attendance for non-existent or deleted camera ID: {camera_id}")
        attendance_data = AttendanceEventCreate(
            student_id=decision.student_id,
            camera_id=camera_id,
            event_type="ENTRY" if decision.event_type == "ENTRY" else "EXIT",
            confidence=int(round(decision.confidence))
        )
        try:
            crud.create_attendance_event(db, attendance_data)
        except Exception:
            db.rollback()
            raise
        session_info['last_attendance_time'] = time.time()
        logger.info(f"Recorded attendance for student {decision.student_id}: {decision.event_type} "
                    f"(score: {decision.confidence:.1f}, camera {camera_id})")

    return record_event

@router.post("/{camera_id}/start")
async def start_video_processing(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_operator)
):
    """Start video processing for a specific camera"""

    # Get camera from database
    camera = db.query(models.Camera).filter(models.Camera.id == camera_id).first()
    if camera is None or camera.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Camera not found")

    if not camera.is_active:
        raise HTTPException(status_code=400, detail="Camera is not active")

    with _sessions_lock:
        # Check if already processing for this camera
        if camera_id in active_sessions:
            raise HTTPException(status_code=400, detail="Video processing already running for this camera")
        other = source_in_use(camera.source)
        if other is not None:
            raise HTTPException(status_code=409, detail=f"Camera {other} is already processing the same source "
                                                        f"({mask_source(camera.source)}); stop it first")
        # Reserve the slot so two simultaneous requests cannot both open the device
        active_sessions[camera_id] = {"source": camera.source, "starting": True}

    video_service = None
    try:
        person_detector = services.PersonDetectionService(model_path=settings.YOLO_MODEL_PATH)
        person_detector.check_model_file()  # never let Ultralytics download a missing model

        # Face model chosen by FACE_RECOGNITION_MODEL; its files are checked before the
        # camera opens, so a missing model is a clear 503, never a silent "no faces"
        recognition_model = face_models.active_model()
        limits = face_models.thresholds(recognition_model)
        sface = None
        if recognition_model == face_models.SFACE:
            sface = face_models.SFaceModel()
            try:
                sface.check()
            except face_models.FaceModelUnavailable as e:
                raise services.ModelUnavailableError(str(e))

        attendance_engine = get_attendance_engine()

        source = (resolve_video_source(camera.source, settings.VIDEO_FILES_DIR)
                  if camera.source_type == "video" else camera.source)
        # Create video processing service
        video_service = services.VideoProcessingService(
            video_source=source,  # device index, file path or stream URL
            frame_width=camera.width,
            frame_height=camera.height,
            fps=camera.fps,
            camera_type=camera.camera_type,  # Pass camera type (IN/OUT)
            camera_id=camera_id,
            attendance_engine=attendance_engine,
            face_detector=services.FaceDetectionService(
                match_tolerance=limits["match_tolerance"],
                ambiguity_margin=settings.FACE_MATCH_AMBIGUITY_MARGIN,
                num_jitters=settings.FACE_ENCODING_JITTERS,
                upsample=settings.FACE_DETECTION_UPSAMPLE,
                min_face_size=settings.FACE_MIN_SIZE_PIXELS,
                min_sharpness=settings.FACE_MIN_SHARPNESS,
                recognition_model=recognition_model,
                sface_model=sface,
            ),
            allow_synthetic_source=settings.ENABLE_SYNTHETIC_CAMERA,
            unknown_tracker=get_unknown_tracker(),
            source_type=camera.source_type,
            person_detector=person_detector,
            auto_reconnect=settings.CAMERA_AUTO_RECONNECT,
            reconnect_max_delay=settings.CAMERA_RECONNECT_MAX_DELAY_SECONDS,
            stale_after_seconds=settings.CAMERA_STALE_AFTER_SECONDS,
            latest_frame_only=settings.CAMERA_LATEST_FRAME_ONLY,
            open_timeout_seconds=settings.CAMERA_OPEN_TIMEOUT_SECONDS,
            read_timeout_seconds=settings.CAMERA_READ_TIMEOUT_SECONDS,
            zone=camera.classroom_id,  # attendance state per classroom
        )

        if camera.source_type == "rtsp":
            # One classified check (unreachable / login rejected / wrong channel ...) instead of an
            # unexplained "could not open"; at most one login attempt (device lockout rules)
            pre = await run_in_threadpool(rtsp_check.check, camera.source, settings.CAMERA_OPEN_TIMEOUT_SECONDS)
            if pre.ok is False:
                raise services.CameraOpenError(pre.detail)
        # Start the video processing service (raises CameraOpenError if the source can't be opened).
        # Opening a network stream can take seconds: off the event loop so the API stays responsive.
        await run_in_threadpool(video_service.start)

        # Load known faces and today's attendance state from database
        video_service.load_known_faces_from_db(db)
        seed_engine_from_db(attendance_engine, db)

        # Coverage record: lets period attendance tell "absent" from "nobody was watching"
        run = models.CameraRun(camera_id=camera_id, started_at=utc_now_naive(), feed_kind=video_service.feed_kind)
        db.add(run)
        audit(db, current_user, "video.start", "camera", camera_id, {"feed_kind": video_service.feed_kind})
        db.commit()

        # Store session info
        session_info = {
            'service': video_service,
            'camera_id': camera_id,
            'camera_name': camera.name,
            'source': camera.source,
            'start_time': time.time(),
            'frame_count': 0,
            'last_attendance_time': 0,
            'thread': None,
            'stop_event': threading.Event(),
            'run_id': run.id,
        }

        # Start background processing thread
        def process_video_frames():
            frame_counter = 0
            # Each worker owns its DB session; the request session closes when the request ends
            worker_db = SessionLocal()
            record_event = make_event_recorder(worker_db, camera_id, session_info)
            faces_version = services.get_known_faces_version()
            last_faces_reload = time.time()
            stop_event = session_info['stop_event']

            try:
                video_service.warm_up()  # load the models now, not on the first camera frame
                while not stop_event.is_set():
                    try:
                        # Pick up newly enrolled / removed faces
                        now = time.time()
                        current_version = services.get_known_faces_version()
                        if (current_version != faces_version
                                or now - last_faces_reload >= settings.FACE_RELOAD_INTERVAL_SECONDS):
                            worker_db.expire_all()  # Don't reuse stale cached student rows
                            if video_service.load_known_faces_from_db(worker_db):
                                faces_version = current_version
                            last_faces_reload = now

                        # Process frame; ENTRY/EXIT decisions are persisted via record_event
                        annotated_frame, attendance_decisions = video_service.process_frame(
                            worker_db, record_event=record_event
                        )
                        # Coverage only while frames really arrive: an outage closes the
                        # coverage record (so it can never count as "watched, nobody came")
                        track_coverage(worker_db, session_info, video_service, camera_id)

                        frame_counter += 1
                        session_info['frame_count'] = frame_counter

                        if annotated_frame is None:
                            # Read failed / reconnecting / file ended; the status endpoint says why
                            stop_event.wait(0.5)
                            continue

                        # Sleep to control processing rate (aim for ~10 FPS processing)
                        stop_event.wait(0.1)

                    except Exception as e:
                        logger.error(f"Error in video processing thread (camera {camera_id}): {mask_source(e)}")
                        worker_db.rollback()
                        stop_event.wait(1)  # Wait longer on error
            finally:
                worker_db.close()

        thread = threading.Thread(target=process_video_frames, daemon=True, name=f"camera-{camera_id}")
        session_info['thread'] = thread
        with _sessions_lock:
            active_sessions[camera_id] = session_info
        thread.start()

        logger.info(f"Started video processing for camera {camera_id} ({camera.name})")

        return {
            "message": f"Video processing started for camera {camera.name}",
            "camera_id": camera_id,
            "source": mask_source(camera.source),
            "feed_kind": video_service.feed_kind,
        }

    except services.ModelUnavailableError as e:
        active_sessions.pop(camera_id, None)
        raise HTTPException(status_code=503, detail=str(e))
    except services.CameraOpenError as e:
        active_sessions.pop(camera_id, None)
        logger.error(f"Camera {camera_id} ({camera.name}) could not be opened: {e}")
        raise HTTPException(status_code=503, detail=f"Camera '{camera.name}' could not be opened: {e}")
    except Exception as e:
        active_sessions.pop(camera_id, None)
        db.rollback()
        if video_service is not None:
            video_service.stop()
        logger.error(f"Failed to start video processing for camera {camera_id}: {mask_source(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start video processing: {mask_source(e)}")

def _is_running(camera_id: int) -> bool:
    info = active_sessions.get(camera_id)
    return info is not None and not info.get("starting")


def _running_sessions():
    return [(cid, info) for cid, info in list(active_sessions.items()) if not info.get("starting")]


def stop_session(camera_id: int, reason: str = "stopped by operator") -> Dict[str, Any]:
    """Stop one worker: signal, join, release the source, close its coverage record"""
    session_info = active_sessions[camera_id]
    session_info['stop_event'].set()
    if session_info.get('thread') and session_info['thread'].is_alive():
        session_info['thread'].join(timeout=5.0)
    session_info['service'].stop()
    with _sessions_lock:
        active_sessions.pop(camera_id, None)
    _close_run(session_info.get('run_id'), reason)
    return session_info


def stop_all_sessions(reason: str = "server shutdown"):
    for camera_id, _ in _running_sessions():
        try:
            stop_session(camera_id, reason)
        except Exception as e:
            logger.error(f"Error stopping camera {camera_id} on shutdown: {mask_source(e)}")


@router.post("/{camera_id}/stop")
async def stop_video_processing(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_operator)
):
    """Stop video processing for a specific camera"""

    if not _is_running(camera_id):
        raise HTTPException(status_code=404, detail="No active video processing session for this camera")

    try:
        session_info = stop_session(camera_id)
        audit(db, current_user, "video.stop", "camera", camera_id, None, commit=True)

        logger.info(f"Stopped video processing for camera {camera_id}")

        return {
            "message": f"Video processing stopped for camera {camera_id}",
            "frames_processed": session_info['frame_count'],
            "duration_seconds": time.time() - session_info['start_time']
        }

    except Exception as e:
        logger.error(f"Error stopping video processing for camera {camera_id}: {mask_source(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to stop video processing: {mask_source(e)}")

@router.get("/{camera_id}/status")
async def get_video_processing_status(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_viewer)
):
    """Get status of video processing for a specific camera"""

    if not _is_running(camera_id):
        return {
            "camera_id": camera_id,
            "is_processing": False,
            "message": "Video processing not active"
        }

    session_info = active_sessions[camera_id]
    stats = session_info['service'].get_stats()

    return {
        "camera_id": camera_id,
        "is_processing": True,
        "frames_processed": session_info['frame_count'],
        "duration_seconds": time.time() - session_info['start_time'],
        "error": stats.get('last_error'),
        "service_stats": stats
    }


def _session_summary(camera_id: int, session_info: Dict[str, Any], camera: Optional[models.Camera]) -> Dict[str, Any]:
    stats = session_info['service'].get_stats()
    return {
        "camera_id": camera_id,
        "camera_name": camera.name if camera else session_info.get('camera_name', f"Camera {camera_id}"),
        "camera_type": camera.camera_type if camera else stats.get('camera_type'),
        "classroom_id": camera.classroom_id if camera else None,
        "is_processing": True,
        "frames_processed": session_info['frame_count'],
        "duration_seconds": time.time() - session_info['start_time'],
        "service_stats": stats
    }


@router.get("/sessions")
async def get_all_video_processing_sessions(
    db: Session = Depends(get_db),
    current_user=Depends(require_viewer)
):
    """Get all active video processing sessions"""
    active_list = []
    for camera_id, session_info in _running_sessions():
        camera = db.query(models.Camera).filter(models.Camera.id == camera_id).first()
        active_list.append(_session_summary(camera_id, session_info, camera))

    return {
        "active_sessions": active_list,
        "count": len(active_list)
    }


def occupancy_snapshot() -> Dict[str, Any]:
    """In-memory occupancy shared by all cameras of this process"""
    now = time.time()
    tracker = services.get_shared_unknown_tracker_if_exists()
    engine = services.get_shared_attendance_engine_if_exists()
    snap = tracker.snapshot(now) if tracker is not None else None
    return {
        "known_inside_count": engine.inside_count(now) if engine is not None else None,
        "unknown_inside_count": snap["unknown_inside_count"] if snap else 0,
        "unknown_entries_today": snap["unknown_entries_today"] if snap else 0,
        "unknown_tracking_since": snap["unknown_tracking_since"] if snap else None,
        "unknown_tracking_active": snap is not None,
    }


@router.get("/live")
async def get_live_overview(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
                            current_user=Depends(require_viewer)):
    """Everything the live view needs in one request: running cameras with health,
    recent recognitions/events from all cameras (newest first) and occupancy."""
    sessions, observations = [], []
    cameras = {c.id: c for c in db.query(models.Camera).all()}
    for camera_id, session_info in _running_sessions():
        camera = cameras.get(camera_id)
        sessions.append(_session_summary(camera_id, session_info, camera))
        for obs in session_info['service'].get_recent_observations(limit):
            observations.append({**obs, "camera_name": camera.name if camera else None})
    observations.sort(key=lambda o: o["timestamp"], reverse=True)
    return {"sessions": sessions, "observations": observations[:limit], "occupancy": occupancy_snapshot(),
            "server_time": time.time()}


@router.get("/{camera_id}/observations")
async def get_camera_observations(camera_id: int, limit: int = Query(50, ge=1, le=200),
                                  current_user=Depends(require_viewer)):
    if not _is_running(camera_id):
        raise HTTPException(status_code=404, detail="No active video processing session for this camera")
    return active_sessions[camera_id]['service'].get_recent_observations(limit)


@router.get("/{camera_id}/frame")
async def get_latest_frame(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(require_viewer)
):
    """Get the latest processed frame from video processing"""

    if not _is_running(camera_id):
        raise HTTPException(status_code=404, detail="No active video processing session for this camera")

    try:
        session_info = active_sessions[camera_id]
        video_service = session_info['service']

        # Get the latest frame as JPEG bytes
        jpeg_bytes = video_service.get_latest_frame_jpeg()

        if jpeg_bytes is None:
            # Return a placeholder or empty response if no frame is available
            raise HTTPException(status_code=404, detail="No frame available yet")

        # Frames are live data: never cache them
        return Response(content=jpeg_bytes, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    except HTTPException:
        # Keep intended statuses (e.g. 404 "No frame available yet") instead of wrapping them in a 500
        raise
    except Exception as e:
        logger.error(f"Error getting frame for camera {camera_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get frame: {str(e)}")