"""
Staff (professor) portal: a professor's weekly timetable and the attendance register of
one class period, built from the existing camera attendance data.

Access: professor accounts see only the timetable periods assigned to them
(timetable_periods.professor_user_id); administrators see every period. Every endpoint
checks this server-side; no other role can use them.

How a register is built (nothing is invented; nothing is written until Save):
  - Roster: active students whose class_name matches the period (attendance_logic.roster_for).
  - Camera result: attendance_logic.compute_period, the same rules as every staff report.
  - Evidence: the ENTRY event behind a PRESENT/LATE result. Only an entry seen by an IN
    camera assigned to THIS period's classroom (or a manual event recorded by an operator)
    supports the suggestion. An entry seen by any other camera only shows the student was
    in the building, so the suggestion is UNVERIFIED.
  - A student the cameras did not see is NEVER suggested as absent: the suggestion is
    UNVERIFIED (ai_status NOT_DETECTED when the classroom camera was running) until the
    professor confirms. Coverage gaps (no classroom camera, camera not running, camera
    stopped reporting) are flagged for review.
  - Final decisions are attendance_overrides rows (the existing audited mechanism), so
    every report and the student portal use them. attendance_registers records who
    submitted the register and when; its version number rejects saves from stale copies.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import attendance_logic, models
from app.database import get_db
from app.permissions import audit, require_professor_or_admin, role_of
from app.routers.timetable import DAY_NAMES, _check_day
from app.schemas import ensure_utc

router = APIRouter()

FINAL_STATUSES = ("PRESENT", "LATE", "ABSENT", "EXCUSED")
DETECTED = ("PRESENT", "LATE")
# A running camera writes a heartbeat every 30 s (routers/video.py); after this long
# without one its coverage is treated as ended (crashed or disconnected)
HEARTBEAT_STALE = timedelta(seconds=90)


# --- helpers --------------------------------------------------------------------------

def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    value = ensure_utc(value)
    return value.isoformat().replace("+00:00", "Z") if value else None


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_admin(user) -> bool:
    return role_of(user) == "admin"


def _period_for(db: Session, period_id: int, user) -> models.TimetablePeriod:
    """The period, if this user may see it: its assigned professor, or an admin"""
    period = db.query(models.TimetablePeriod).filter(models.TimetablePeriod.id == period_id).first()
    if period is None:
        raise HTTPException(status_code=404, detail="Period not found")
    if not _is_admin(user) and period.professor_user_id != user.id:
        raise HTTPException(status_code=403, detail="This class is not assigned to you")
    return period


def _usernames(db: Session, ids) -> Dict[int, str]:
    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    return dict(db.query(models.User.id, models.User.username).filter(models.User.id.in_(ids)).all())


def _classroom_coverage(db: Session, period: models.TimetablePeriod, day: date, now: datetime) -> Dict:
    """How much of the class (start to end, or to now) this classroom's own IN cameras
    delivered frames. Uses heartbeats, so a camera that crashed without stopping does not
    count as watching; a coverage record closed as "feed lost" marks an outage."""
    window_start, end, _, _ = attendance_logic.period_window(period, day)
    window_end = min(end, now)
    cameras = db.query(models.Camera).filter(models.Camera.classroom_id == period.classroom_id,
                                             models.Camera.camera_type == "IN",
                                             models.Camera.deleted_at.is_(None)).all()
    result = {"classroom_cameras": [c.name for c in cameras], "covered_percent": None, "outage": False}
    if not cameras:
        result["state"] = "no_classroom_camera"
        return result
    if window_end <= window_start:
        result["state"] = "not_applicable"
        return result
    to_stored = attendance_logic.local_naive_to_stored
    to_local = attendance_logic.stored_to_local_naive
    runs = db.query(models.CameraRun).filter(
        models.CameraRun.camera_id.in_([c.id for c in cameras]),
        models.CameraRun.started_at < to_stored(window_end)).all()
    intervals = []
    for run in runs:
        if run.stopped_at is not None:
            run_end = to_local(run.stopped_at)
            if (run.stop_reason or "").startswith("feed lost") and window_start <= run_end < window_end:
                result["outage"] = True  # the stream dropped during the class
        else:
            beat = to_local(run.last_heartbeat_at or run.started_at)
            run_end = now if now - beat <= HEARTBEAT_STALE else beat
            if now - beat > HEARTBEAT_STALE and beat < window_end:
                result["outage"] = True  # stopped reporting during the period
        lo, hi = max(to_local(run.started_at), window_start), min(run_end, window_end)
        if hi > lo:
            intervals.append((lo, hi))
    # Union of the intervals (two cameras may overlap)
    covered, cursor = 0.0, None
    for lo, hi in sorted(intervals):
        if cursor is None or lo > cursor:
            covered += (hi - lo).total_seconds()
            cursor = hi
        elif hi > cursor:
            covered += (hi - cursor).total_seconds()
            cursor = hi
    percent = round(covered / (window_end - window_start).total_seconds() * 100.0, 1)
    result["covered_percent"] = percent
    result["state"] = "none" if percent == 0 else ("full" if percent >= 99.9 else "partial")
    return result


def _compute(db: Session, period: models.TimetablePeriod, day: date, now: datetime):
    """compute_period for this period, chained with the class's earlier periods that day
    (for the did-not-return-after-break check), as the timetable endpoints do"""
    ctx = attendance_logic.load_day(db, day, now)
    roster = attendance_logic.roster_for(db, period.class_name)
    previous = None
    for other in attendance_logic.periods_on(db, day, class_name=period.class_name):
        result = attendance_logic.compute_period(db, other, day, now, ctx, roster, previous)
        if other.id == period.id:
            return result, ctx
        previous = result
    return attendance_logic.compute_period(db, period, day, now, ctx, roster), ctx


# attendance_logic exceptions shown to the professor as review flags (with their reason
# text). ABSENT is not among them: a missing detection is a NOT_DETECTED flag instead.
REVIEW_EXCEPTIONS = ("LATE_ARRIVAL", "LEFT_DURING_PERIOD", "EARLY_DEPARTURE", "OUT_OF_CLASS",
                     "DID_NOT_RETURN_AFTER_BREAK")


def build_register(db: Session, period: models.TimetablePeriod, day: date, now: Optional[datetime] = None) -> Dict:
    now = now or datetime.now()
    result, ctx = _compute(db, period, day, now)
    start, end, window_start, _ = attendance_logic.period_window(period, day)
    coverage = _classroom_coverage(db, period, day, now)
    classroom_cams = {c.id for c in db.query(models.Camera).filter(models.Camera.classroom_id == period.classroom_id,
                                                                      models.Camera.camera_type == "IN").all()}
    camera_names = dict(db.query(models.Camera.id, models.Camera.name).all())
    overrides = {o.student_id: o for o in db.query(models.AttendanceOverride).filter_by(period_id=period.id, date=day).all()}
    register = db.query(models.AttendanceRegister).filter_by(period_id=period.id, date=day).first()
    names = _usernames(db, [o.created_by_user_id for o in overrides.values()]
                       + ([register.submitted_by_user_id, register.first_submitted_by_user_id] if register else []))
    classroom = db.query(models.Classroom).filter(models.Classroom.id == period.classroom_id).first()

    rows = []
    for row in result["students"]:
        computed = row["computed_status"]
        # Same evidence rule as the period result: this classroom's cameras (+ manual events)
        events = attendance_logic.period_events(ctx, period.classroom_id, row["student_db_id"])
        support = None
        if computed in DETECTED:
            if row["inside_at_start"]:
                support = next((e for e in reversed(events) if e.at < start and e.event_type == "ENTRY"), None)
            else:
                support = next((e for e in events if e.event_type == "ENTRY" and start <= e.at < end), None)

        flags = []
        evidence = None
        if computed in DETECTED and support is not None:
            if support.source != "camera":
                kind = "manual_event"
            elif support.camera_id in classroom_cams:
                kind = "classroom_camera"
            else:
                kind = "other_camera"
            evidence = {"kind": kind, "at": support.at.isoformat(timespec="seconds"),
                        "camera": camera_names.get(support.camera_id)}
            if kind == "other_camera":
                ai_status, suggested = "SEEN_ELSEWHERE", "UNVERIFIED"
                flags.append({"code": "BUILDING_ONLY",
                              "message": f"Seen by {evidence['camera'] or 'another camera'} at {support.at:%H:%M}, "
                                         f"not by this classroom's camera: in the building, not necessarily in class"})
            else:
                ai_status, suggested = computed, computed
                if kind == "manual_event":
                    flags.append({"code": "MANUAL_EVENT", "message": "Entry was recorded manually by an operator, "
                                                                     "not by a camera"})
        elif computed == "ABSENT":
            ai_status, suggested = "NOT_DETECTED", "UNVERIFIED"
            if coverage["state"] == "no_classroom_camera":
                flags.append({"code": "NO_CLASSROOM_CAMERA",
                              "message": "This classroom has no entry camera; only other cameras were running"})
            else:
                flags.append({"code": "NOT_DETECTED",
                              "message": "Not recognised by the classroom camera while it was running. "
                                         "Confirm absent only if you know they were not in class"})
        elif computed == "UNVERIFIED":
            ai_status, suggested = "NO_COVERAGE", "UNVERIFIED"
            flags.append({"code": "NO_CAMERA_COVERAGE",
                          "message": "The classroom camera did not cover this period (not running, outage or "
                                     "started late), so not seeing a student proves nothing"})
        elif computed == "NOT_ARRIVED":
            ai_status, suggested = "NOT_DETECTED", "UNVERIFIED"
            flags.append({"code": "NOT_DETECTED_YET", "message": "Not recognised yet; the period is still running"})
        else:  # NOT_STARTED, HOLIDAY, or a detected status without a stored entry (cannot happen)
            ai_status, suggested = computed, computed if computed in ("NOT_STARTED", "HOLIDAY") else "UNVERIFIED"

        if evidence is None and computed not in ("NOT_STARTED", "HOLIDAY"):
            # Not seen by this classroom's camera: say so if another camera saw them meanwhile
            # (a review hint for the professor, never evidence of presence)
            elsewhere = next((e for e in ctx.events.get(row["student_db_id"], [])
                              if e.event_type == "ENTRY" and e.source == "camera" and e.camera_id not in classroom_cams
                              and window_start <= e.at < end), None)
            if elsewhere is not None:
                ai_status = "SEEN_ELSEWHERE"
                evidence = {"kind": "other_camera", "at": elsewhere.at.isoformat(timespec="seconds"),
                            "camera": camera_names.get(elsewhere.camera_id)}
                flags.insert(0, {"code": "BUILDING_ONLY",
                                 "message": f"Seen by {evidence['camera'] or 'another camera'} at {elsewhere.at:%H:%M}, "
                                            f"not by this classroom's camera: in the building, not necessarily in class"})

        if suggested == "UNVERIFIED" and coverage["outage"]:
            flags.append({"code": "CAMERA_OUTAGE", "message": "The classroom camera stopped reporting during this period"})
        elif suggested == "UNVERIFIED" and coverage["state"] == "partial":
            flags.append({"code": "PARTIAL_COVERAGE",
                          "message": f"The classroom camera covered only {coverage['covered_percent']}% of the period"})
        for exception in row["exceptions"]:
            if exception["type"] in REVIEW_EXCEPTIONS:
                flags.append({"code": exception["type"], "message": exception["reason"]})

        override = overrides.get(row["student_db_id"])
        final = None
        if override is not None:
            final = {"status": override.status, "reason": override.reason,
                     "by": names.get(override.created_by_user_id),
                     "at": _utc_iso(override.updated_at or override.created_at)}
        rows.append({
            "student_db_id": row["student_db_id"], "student_id": row["student_id"], "name": row["name"],
            "ai_status": ai_status, "suggested_status": suggested, "evidence": evidence,
            "arrived_at": row["arrived_at"], "left_at": row["left_at"], "minutes_inside": row["minutes_inside"],
            "review_flags": flags, "final": final,
            "status": final["status"] if final else suggested,
        })

    counts = {s: 0 for s in ("PRESENT", "LATE", "ABSENT", "EXCUSED", "UNVERIFIED")}
    for row in rows:
        if row["status"] in counts:
            counts[row["status"]] += 1
    phase = result["phase"]
    if not period.is_active:
        blocked = "This period has been removed from the timetable"
    elif result["holiday"]:
        blocked = f"Holiday: {result['holiday']}"
    elif phase == "upcoming":
        blocked = "The class has not started yet"
    else:
        blocked = None
    return {
        "period": {"id": period.id, "course_code": period.course_code, "course_name": period.course_name,
                   "class_name": period.class_name, "classroom": classroom.name if classroom else None,
                   "day_name": DAY_NAMES[period.day_of_week], "start_time": result["start_time"],
                   "end_time": result["end_time"], "grace_minutes": result["grace_minutes"],
                   "is_active": period.is_active,
                   "professor": _usernames(db, [period.professor_user_id]).get(period.professor_user_id)},
        "date": day.isoformat(), "phase": phase, "holiday": result["holiday"],
        "coverage": coverage,
        "can_save": blocked is None and len(rows) > 0,
        "save_blocked_reason": blocked if blocked else (None if rows else "No students are on this class's roster"),
        "register": {
            "submitted": register is not None,
            "version": register.version if register else 0,
            "first_submitted_by": names.get(register.first_submitted_by_user_id) if register else None,
            "first_submitted_at": _utc_iso(register.first_submitted_at) if register else None,
            "submitted_by": names.get(register.submitted_by_user_id) if register else None,
            "submitted_at": _utc_iso(register.submitted_at) if register else None,
        },
        "counts": counts,
        "confirmed_count": sum(1 for r in rows if r["final"]),
        "roster_size": len(rows),
        "students": rows,
    }


# --- weekly timetable -----------------------------------------------------------------

@router.get("/timetable")
async def weekly_timetable(week_of: Optional[date] = Query(None, description="Any date in the week; default today"),
                           professor_id: Optional[int] = Query(None, description="Admins: one professor's periods"),
                           db: Session = Depends(get_db), current_user=Depends(require_professor_or_admin)):
    now = datetime.now()
    week_start = (week_of or now.date()) - timedelta(days=(week_of or now.date()).weekday())
    query = db.query(models.TimetablePeriod).filter(models.TimetablePeriod.is_active == True)  # noqa: E712
    if not _is_admin(current_user):
        query = query.filter(models.TimetablePeriod.professor_user_id == current_user.id)
    elif professor_id is not None:
        query = query.filter(models.TimetablePeriod.professor_user_id == professor_id)
    periods = query.order_by(models.TimetablePeriod.day_of_week, models.TimetablePeriod.start_time).all()
    week_end = week_start + timedelta(days=6)
    holidays = {h.date: h.name for h in db.query(models.Holiday).filter(models.Holiday.date >= week_start,
                                                                          models.Holiday.date <= week_end).all()}
    registers = {}
    if periods:
        for reg in db.query(models.AttendanceRegister).filter(
                models.AttendanceRegister.period_id.in_([p.id for p in periods]),
                models.AttendanceRegister.date >= week_start, models.AttendanceRegister.date <= week_end).all():
            registers[(reg.period_id, reg.date)] = reg
    rooms = dict(db.query(models.Classroom.id, models.Classroom.name).all())
    names = _usernames(db, [p.professor_user_id for p in periods]
                       + [r.submitted_by_user_id for r in registers.values()])

    days = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        items = []
        for p in periods:
            if p.day_of_week != offset:
                continue
            start, end = datetime.combine(day, p.start_time), datetime.combine(day, p.end_time)
            added = attendance_logic.stored_to_local_naive(p.created_at).date() if p.created_at else None
            reg = registers.get((p.id, day))
            if added is not None and day < added:
                state = "not_scheduled"  # the period did not exist yet on this date
            elif day in holidays:
                state = "holiday"
            elif reg is not None:
                state = "submitted"
            elif now < start:
                state = "upcoming"
            elif now < end:
                state = "in_progress"
            else:
                state = "awaiting_review"
            items.append({
                "period_id": p.id, "date": day.isoformat(), "start_time": f"{p.start_time:%H:%M}",
                "end_time": f"{p.end_time:%H:%M}", "course_code": p.course_code, "course_name": p.course_name,
                "class_name": p.class_name, "classroom": rooms.get(p.classroom_id),
                "professor": names.get(p.professor_user_id), "state": state,
                "submitted_by": names.get(reg.submitted_by_user_id) if reg else None,
                "submitted_at": _utc_iso(reg.submitted_at) if reg else None,
            })
        days.append({"date": day.isoformat(), "day_name": DAY_NAMES[offset], "holiday": holidays.get(day),
                     "periods": items})
    return {"week_start": week_start.isoformat(), "week_end": week_end.isoformat(), "today": now.date().isoformat(),
            "assigned_periods": len(periods), "days": days}


# --- register -------------------------------------------------------------------------

@router.get("/periods/{period_id}/register")
async def get_register(period_id: int, day: date = Query(..., alias="date"), db: Session = Depends(get_db),
                       current_user=Depends(require_professor_or_admin)):
    period = _period_for(db, period_id, current_user)
    _check_day(period, day)
    return build_register(db, period, day)


class Decision(BaseModel):
    student_db_id: int
    status: str = Field(..., pattern="^(PRESENT|LATE|ABSENT|EXCUSED)$")
    reason: Optional[str] = Field(None, max_length=500)
    # The final status the client loaded for this student (None = not decided yet). A
    # mismatch means someone else changed it meanwhile; the save is refused.
    base_status: Optional[str] = Field(None, pattern="^(PRESENT|LATE|ABSENT|EXCUSED)$")


class RegisterSave(BaseModel):
    expected_version: int = Field(..., ge=0, description="register.version the client loaded (0 = never submitted)")
    decisions: List[Decision] = Field(..., max_length=2000)


@router.put("/periods/{period_id}/register")
async def save_register(period_id: int, body: RegisterSave, day: date = Query(..., alias="date"),
                        db: Session = Depends(get_db), current_user=Depends(require_professor_or_admin)):
    """Submit (or resubmit) the whole register. Every student on the roster needs a final
    decision. Refused (409, nothing written) if the register or any decision changed since
    the client loaded it. A reason is required to excuse a student, to mark absent a student
    the classroom camera recorded, and to change an earlier decision."""
    period = _period_for(db, period_id, current_user)
    _check_day(period, day)
    current = build_register(db, period, day)
    if not current["can_save"]:
        raise HTTPException(status_code=400, detail=current["save_blocked_reason"])

    register = db.query(models.AttendanceRegister).filter_by(period_id=period_id, date=day).first()
    if (register.version if register else 0) != body.expected_version:
        who = f" by {current['register']['submitted_by']}" if register else ""
        raise HTTPException(status_code=409, detail=f"This register was saved{who} after you opened it. "
                                                    "Reload to see the latest version; your changes are still on screen.")

    roster = {r["student_db_id"]: r for r in current["students"]}
    seen = set()
    for decision in body.decisions:
        if decision.student_db_id not in roster:
            raise HTTPException(status_code=400, detail=f"Student #{decision.student_db_id} is not on this class's roster")
        if decision.student_db_id in seen:
            raise HTTPException(status_code=400, detail="Each student may appear only once")
        seen.add(decision.student_db_id)
    missing = [r["name"] for sid, r in roster.items() if sid not in seen]
    if missing:
        raise HTTPException(status_code=400, detail=f"Decide every student before saving ({len(missing)} still "
                                                    f"unverified: {', '.join(missing[:5])}{'…' if len(missing) > 5 else ''})")

    conflicts, needs_reason, changes = [], [], []
    for decision in body.decisions:
        row = roster[decision.student_db_id]
        before = row["final"]["status"] if row["final"] else None
        if decision.base_status != before:
            conflicts.append(row["name"])
            continue
        reason = (decision.reason or "").strip()
        camera_backed = row["evidence"] is not None and row["evidence"]["kind"] == "classroom_camera"
        # Only a new or changed decision needs a reason; an unchanged saved one already has its own
        unchanged = before is not None and decision.status == before
        required = not unchanged and (decision.status == "EXCUSED" or before is not None
                                      or (camera_backed and decision.status == "ABSENT"))
        if required and len(reason) < 3:
            needs_reason.append(row["name"])
        if before is None or decision.status != before:
            changes.append((decision, row, before, reason))
    if conflicts:
        raise HTTPException(status_code=409, detail=f"Someone else changed {', '.join(conflicts[:5])} after you opened "
                                                    "the register. Reload to see the latest decisions; your changes are "
                                                    "still on screen.")
    if needs_reason:
        raise HTTPException(status_code=400, detail=f"Give a reason (at least 3 characters) for "
                                                    f"{', '.join(needs_reason[:5])}: excusing a student, marking absent a "
                                                    "student the camera recorded, or changing a saved decision needs one.")

    user_id = current_user.id
    now_utc = _now_utc_naive()
    audit_changes = []
    for decision, row, before, reason in changes:
        if not reason:
            kind = row["evidence"]["kind"] if row["evidence"] else None
            if decision.status == row["suggested_status"] and kind == "classroom_camera":
                reason = "Confirmed by professor; classroom camera evidence agrees"
            elif decision.status == row["suggested_status"] and kind == "manual_event":
                reason = "Confirmed by professor; manual entry event agrees"
            else:
                reason = "Confirmed by professor in the class register"
        override = db.query(models.AttendanceOverride).filter_by(period_id=period_id, date=day,
                                                                 student_id=decision.student_db_id).first()
        if override is None:
            db.add(models.AttendanceOverride(period_id=period_id, date=day, student_id=decision.student_db_id,
                                             status=decision.status, reason=reason, created_by_user_id=user_id))
        else:
            override.status, override.reason, override.created_by_user_id = decision.status, reason, user_id
        audit_changes.append({"student_db_id": decision.student_db_id, "student_id": row["student_id"],
                              "ai_status": row["ai_status"], "before": before, "after": decision.status,
                              "reason": reason})

    first = register is None
    if first:
        register = models.AttendanceRegister(period_id=period_id, date=day, version=1,
                                             first_submitted_by_user_id=user_id, first_submitted_at=now_utc,
                                             submitted_by_user_id=user_id, submitted_at=now_utc)
        db.add(register)
    else:
        register.version += 1
        register.submitted_by_user_id, register.submitted_at = user_id, now_utc
    final_counts = {s: 0 for s in FINAL_STATUSES}
    for decision in body.decisions:
        final_counts[decision.status] += 1
    audit(db, current_user, "attendance.register_submit" if first else "attendance.register_update", "period",
          period_id, {"date": day, "version": register.version, "counts": final_counts, "changes": audit_changes})
    try:
        db.commit()
    except IntegrityError:  # another save of the same register landed first
        db.rollback()
        raise HTTPException(status_code=409, detail="This register was just saved by someone else. Reload to see the "
                                                    "latest version; your changes are still on screen.")
    saved = build_register(db, period, day)
    saved["changed_count"] = len(audit_changes)
    return saved


# --- history --------------------------------------------------------------------------

@router.get("/history")
async def register_history(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db),
                           current_user=Depends(require_professor_or_admin)):
    """Submitted registers, newest first: the professor's own periods (admins: all)"""
    query = db.query(models.AttendanceRegister, models.TimetablePeriod).join(
        models.TimetablePeriod, models.TimetablePeriod.id == models.AttendanceRegister.period_id)
    if not _is_admin(current_user):
        query = query.filter(models.TimetablePeriod.professor_user_id == current_user.id)
    rows = query.order_by(desc(models.AttendanceRegister.date), desc(models.AttendanceRegister.submitted_at)).limit(limit).all()
    counts: Dict = {}
    if rows:
        for period_id, day, status, n in db.query(
                models.AttendanceOverride.period_id, models.AttendanceOverride.date, models.AttendanceOverride.status,
                func.count()).filter(models.AttendanceOverride.period_id.in_({p.id for _, p in rows})).group_by(
                models.AttendanceOverride.period_id, models.AttendanceOverride.date, models.AttendanceOverride.status).all():
            counts.setdefault((period_id, day), {s: 0 for s in FINAL_STATUSES})[status] = n
    names = _usernames(db, [r.submitted_by_user_id for r, _ in rows])
    return [{
        "period_id": p.id, "date": r.date.isoformat(), "day_name": DAY_NAMES[p.day_of_week],
        "start_time": f"{p.start_time:%H:%M}", "end_time": f"{p.end_time:%H:%M}", "course_code": p.course_code,
        "course_name": p.course_name, "class_name": p.class_name, "version": r.version,
        "submitted_by": names.get(r.submitted_by_user_id), "submitted_at": _utc_iso(r.submitted_at),
        "counts": counts.get((p.id, r.date), {s: 0 for s in FINAL_STATUSES}),
    } for r, p in rows]
