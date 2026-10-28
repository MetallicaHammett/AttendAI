from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from typing import List, Optional
from app.config import settings
from app.database import get_db
from app import attendance_logic, crud, models
from app.permissions import require_viewer as get_current_user
from app.schemas import DashboardStats
from pydantic import BaseModel
from datetime import date, datetime, time, timedelta
from enum import Enum

router = APIRouter()


class TrendDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    STABLE = "stable"


class DailyAttendance(BaseModel):
    date: date
    present: int
    absent: int
    late: int
    total: int


class AttendanceTrend(BaseModel):
    trend: TrendDirection
    change_percentage: float
    period_days: int


class DashboardData(BaseModel):
    stats: DashboardStats
    daily_attendance: List[DailyAttendance]
    trend: AttendanceTrend


def late_after_setting() -> Optional[time]:
    """settings.ATTENDANCE_LATE_AFTER ("HH:MM", local) as a time, or None"""
    value = settings.ATTENDANCE_LATE_AFTER
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        raise HTTPException(status_code=500, detail=f"Invalid ATTENDANCE_LATE_AFTER setting {value!r}; expected HH:MM")


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    stats_dict = crud.get_dashboard_stats(db)
    return DashboardStats(**stats_dict)


@router.get("/daily/{days}", response_model=List[DailyAttendance])
async def get_daily_attendance(days: int = Path(..., ge=1, le=366), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """Per-day attendance from the database, oldest first (see crud.get_daily_attendance)"""
    daily = crud.get_daily_attendance(db, days=days, late_after=late_after_setting())
    return [DailyAttendance(**d) for d in daily]


@router.get("/trend", response_model=AttendanceTrend)
async def get_attendance_trend(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """Last 7 completed days vs the 7 before (see crud.get_attendance_trend)"""
    trend = crud.get_attendance_trend(db, period_days=14, late_after=late_after_setting())
    return AttendanceTrend(**trend)


@router.get("/alerts")
async def get_alerts(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    """Current alerts, each with why it was raised and what it is based on:
      attendance - exceptions computed from recorded attendance events for today's periods
      camera     - running cameras that are disconnected, stale or failing to process
      occupancy  - unregistered people estimated inside (face-matching estimate, not verified)"""
    from app.routers.video import _running_sessions, occupancy_snapshot
    alerts = []
    today = date.today()
    for e in attendance_logic.daily_summary(db, today)["exceptions"]:
        alerts.append({"category": "attendance", "severity": "warning" if e["type"] != "LEFT_DURING_PERIOD" else "info",
                       "type": e["type"], "title": f"{e['name']} ({e['student_id']})",
                       "detail": f"{e['course_name']} {e['start_time']}: {e['reason']}",
                       "basis": "verified: recorded attendance events", "student_db_id": e["student_db_id"],
                       "period_id": e["period_id"], "date": e["date"]})
    cameras = {c.id: c.name for c in db.query(models.Camera).all()}
    for camera_id, info in _running_sessions():
        stats = info["service"].get_stats()
        problem = None
        if stats.get("state") in ("disconnected", "reconnecting"):
            problem = stats.get("last_error") or "Camera disconnected"
        elif stats.get("stale"):
            problem = f"No new frame for {stats.get('frame_age_seconds') or '?'} s"
        elif stats.get("pipeline_error"):
            problem = stats["pipeline_error"]
        elif stats.get("state") == "ended":
            problem = "The recorded video has ended"
        if problem:
            alerts.append({"category": "camera", "severity": "error" if stats.get("state") != "ended" else "info",
                           "type": "CAMERA_" + (stats.get("state") or "error").upper(),
                           "title": cameras.get(camera_id, f"Camera {camera_id}"), "detail": problem,
                           "basis": "camera health", "camera_id": camera_id})
    occupancy = occupancy_snapshot()
    if occupancy["unknown_inside_count"]:
        alerts.append({"category": "occupancy", "severity": "info", "type": "UNREGISTERED_INSIDE",
                       "title": f"{occupancy['unknown_inside_count']} unregistered person(s) estimated inside",
                       "detail": "Counted from faces that matched no enrolled person at the entry camera; "
                                 "an estimate, not a verified identity.",
                       "basis": "computer-vision estimate (unverified)"})
    return {"date": today.isoformat(), "alerts": alerts}


@router.get("/", response_model=DashboardData)
async def get_dashboard_data(db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    stats = await get_dashboard_stats(db, current_user)
    daily = await get_daily_attendance(7, db, current_user)
    trend = await get_attendance_trend(db, current_user)

    return DashboardData(
        stats=stats,
        daily_attendance=daily,
        trend=trend
    )
