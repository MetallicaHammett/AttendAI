"""
Period attendance, presence sessions, exceptions and daily/monthly summaries.

Everything here is computed from persisted data only: attendance_events (camera and
manual), camera_runs (when cameras were actually watching), timetable_periods, holidays
and attendance_overrides. Nothing is written. Times are compared in server-local time;
stored timestamps are naive UTC (see crud.py).

Period status of one student (one class period on one date):
  PRESENT      inside at the start, or first ENTRY no later than start + grace
  LATE         first ENTRY after start + grace and before the end
  ABSENT       no ENTRY in the period window, the period is over, and an IN camera of the
               classroom (or any IN camera if none is assigned) was delivering frames for
               the whole period, apart from at most ATTENDANCE_MAX_COVERAGE_GAP_MINUTES
  UNVERIFIED   no ENTRY, and the camera did not cover the period (not running, outage,
               stalled feed, started late) - the system cannot tell; never reported as absent

Evidence: for a classroom with its own cameras, only events from those cameras and manual
events count for its periods (an entry seen by another classroom's or a gate camera does
not show the student was in this class). Classrooms without cameras use every event.
Coverage comes from camera_runs, which the camera workers keep equal to the time frames
really arrived; an open run whose heartbeat is older than HEARTBEAT_STALE_SECONDS ends there.
  NOT_ARRIVED  no ENTRY yet, period in progress
  NOT_STARTED  the period has not started
  EXCUSED      only via an authorised override
  HOLIDAY      the date is a holiday
An override (operator decision, audited) replaces the computed status; the computed
status is still returned alongside it.

A weekly period counts from the local date it was created (timetable_periods.created_at):
daily and monthly summaries never include occurrences before it existed.

Exceptions (per student, with the reason):
  LATE_ARRIVAL, ABSENT, EARLY_DEPARTURE (left before end - early_departure_minutes and did
  not come back; only once the period is over), OUT_OF_CLASS (left during a period that is
  still running), LEFT_DURING_PERIOD (left and came back), DID_NOT_RETURN_AFTER_BREAK
  (attended the previous period of the same class that day, left, and missed this one).
All exceptions are derived from recorded attendance events ("verified" basis); they
inherit the accuracy limits of face recognition (see docs/LIMITATIONS.md).
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app import crud, models
from app.config import settings

STATUSES = ("PRESENT", "LATE", "ABSENT", "UNVERIFIED", "NOT_ARRIVED", "NOT_STARTED", "EXCUSED", "HOLIDAY")
# Camera workers heartbeat their open coverage record every 30 s while frames arrive
HEARTBEAT_STALE_SECONDS = 90
OVERRIDE_STATUSES = ("PRESENT", "LATE", "ABSENT", "EXCUSED")
ATTENDED = ("PRESENT", "LATE", "EXCUSED")


def local_naive(day: date, at: time) -> datetime:
    return datetime.combine(day, at)


def stored_to_local_naive(ts: datetime) -> datetime:
    """Stored naive-UTC timestamp -> server-local naive datetime"""
    return crud.to_local(ts).replace(tzinfo=None)


def local_naive_to_stored(dt: datetime) -> datetime:
    """Server-local naive datetime -> naive UTC (the stored format)"""
    return dt.astimezone().astimezone(timezone.utc).replace(tzinfo=None)


@dataclass
class Event:
    student_id: int
    event_type: str
    at: datetime  # local naive
    camera_id: Optional[int]
    source: str


@dataclass
class DayContext:
    """Everything about one local day, loaded once and shared by its periods"""
    day: date
    events: Dict[int, List[Event]]  # student_id -> events (chronological)
    runs: List[Tuple[int, datetime, datetime]]  # (camera_id, start, end) local naive
    in_cameras: Dict[int, Optional[int]]  # IN camera id -> classroom_id
    holiday: Optional[str]
    overrides: Dict[Tuple[int, int], models.AttendanceOverride] = field(default_factory=dict)  # (period, student)
    camera_rooms: Dict[int, Optional[int]] = field(default_factory=dict)  # every camera id -> classroom_id


def load_day(db: Session, day: date, now: Optional[datetime] = None) -> DayContext:
    now = now or datetime.now()
    start_utc = crud.local_day_start_utc(day)
    end_utc = crud.local_day_start_utc(day + timedelta(days=1))
    rows = db.query(models.AttendanceEvent).filter(
        crud.timestamp_at_or_after(models.AttendanceEvent.timestamp, start_utc),
        crud.timestamp_before(models.AttendanceEvent.timestamp, end_utc),
    ).all()
    events: Dict[int, List[Event]] = {}
    for row in rows:
        if row.timestamp is None:
            continue
        events.setdefault(row.student_id, []).append(Event(
            row.student_id, row.event_type, stored_to_local_naive(row.timestamp), row.camera_id, row.source or "camera"))
    for items in events.values():
        items.sort(key=lambda e: e.at)

    runs = []
    run_rows = db.query(models.CameraRun).filter(
        crud.timestamp_before(models.CameraRun.started_at, end_utc)).all()
    for run in run_rows:
        stopped = run.stopped_at
        begin = stored_to_local_naive(run.started_at)
        if stopped is not None:
            end = stored_to_local_naive(stopped)
        else:
            # Still open: covering up to now only while its worker keeps heartbeating
            beat = stored_to_local_naive(run.last_heartbeat_at or run.started_at)
            end = now if (now - beat).total_seconds() <= HEARTBEAT_STALE_SECONDS else beat
        if end >= datetime.combine(day, time.min):
            runs.append((run.camera_id, begin, end))

    cameras = db.query(models.Camera).all()
    in_cameras = {c.id: c.classroom_id for c in cameras if c.camera_type == "IN"}
    holiday = db.query(models.Holiday).filter(models.Holiday.date == day).first()
    overrides = {(o.period_id, o.student_id): o
                 for o in db.query(models.AttendanceOverride).filter(models.AttendanceOverride.date == day).all()}
    return DayContext(day, events, runs, in_cameras, holiday.name if holiday else None, overrides,
                      {c.id: c.classroom_id for c in cameras})


def period_events(ctx: DayContext, classroom_id: Optional[int], student_id: int) -> List[Event]:
    """A student's events that count as evidence for a period in this classroom: if the
    classroom has its own cameras, only theirs plus manual events (camera None); otherwise
    every event (no classroom camera to tell rooms apart)."""
    events = ctx.events.get(student_id, [])
    room_cameras = {cid for cid, room in ctx.camera_rooms.items() if room == classroom_id}
    if classroom_id is None or not room_cameras:
        return events
    return [e for e in events if e.camera_id is None or e.camera_id in room_cameras]


def roster_for(db: Session, class_name: str) -> List[models.Student]:
    """Current students of a class group (staff are never on a class roster)"""
    return db.query(models.Student).filter(
        models.Student.class_name == class_name,
        models.Student.person_type == "student",
        models.Student.deleted_at.is_(None),
        models.Student.is_active == True,  # noqa: E712
    ).order_by(models.Student.name).all()


def inside_intervals(events: List[Event], day_start: datetime, until: datetime) -> List[Tuple[datetime, datetime]]:
    """Intervals inside the building from ENTRY/EXIT events. An EXIT without an earlier
    ENTRY that day is ignored; an open ENTRY lasts until `until`."""
    intervals, entered = [], None
    for event in events:
        if event.event_type == "ENTRY":
            if entered is None:
                entered = event.at
        elif event.event_type == "EXIT" and entered is not None:
            intervals.append((entered, event.at))
            entered = None
    if entered is not None and until > entered:
        intervals.append((entered, until))
    return intervals


def _overlap_seconds(intervals, start: datetime, end: datetime) -> float:
    total = 0.0
    for a, b in intervals:
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


def coverage_seconds(ctx: DayContext, classroom_id: Optional[int], start: datetime, end: datetime) -> float:
    """Seconds of [start, end] during which an IN camera of this classroom (any IN camera if
    the classroom has none) was delivering frames (union of its coverage records)"""
    if end <= start:
        return 0.0
    assigned = [cid for cid, room in ctx.in_cameras.items() if room == classroom_id]
    candidates = set(assigned) if assigned else set(ctx.in_cameras)
    pieces = sorted((max(a, start), min(b, end)) for cid, a, b in ctx.runs if cid in candidates and a < end and b > start)
    covered, cursor = 0.0, None
    for lo, hi in pieces:
        if cursor is None or lo > cursor:
            covered += (hi - lo).total_seconds()
            cursor = hi
        elif hi > cursor:
            covered += (hi - cursor).total_seconds()
            cursor = hi
    return covered


def _coverage(ctx: DayContext, classroom_id: Optional[int], start: datetime, end: datetime) -> bool:
    """Did the classroom camera cover [start, end], apart from at most
    ATTENDANCE_MAX_COVERAGE_GAP_MINUTES? Partial coverage (outage, late start) is not
    enough to call anyone absent."""
    if end <= start:
        return False
    gap = (end - start).total_seconds() - coverage_seconds(ctx, classroom_id, start, end)
    return gap <= settings.ATTENDANCE_MAX_COVERAGE_GAP_MINUTES * 60


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def period_window(period: models.TimetablePeriod, day: date) -> Tuple[datetime, datetime, datetime, int]:
    start = local_naive(day, period.start_time)
    end = local_naive(day, period.end_time)
    grace = period.grace_minutes if period.grace_minutes is not None else settings.ATTENDANCE_DEFAULT_GRACE_MINUTES
    window_start = start - timedelta(minutes=settings.ATTENDANCE_EARLY_ARRIVAL_MINUTES)
    return start, end, window_start, grace


def compute_period(db: Session, period: models.TimetablePeriod, day: date, now: Optional[datetime] = None,
                   ctx: Optional[DayContext] = None, roster: Optional[List[models.Student]] = None,
                   previous: Optional[Dict] = None) -> Dict:
    """Attendance of one period on one date (see module docstring). `previous` is the
    result for the class's preceding period that day (for DID_NOT_RETURN_AFTER_BREAK)."""
    now = now or datetime.now()
    ctx = ctx or load_day(db, day, now)
    roster = roster if roster is not None else roster_for(db, period.class_name)
    start, end, window_start, grace = period_window(period, day)
    late_after = start + timedelta(minutes=grace)
    early_departure_before = end - timedelta(minutes=settings.ATTENDANCE_EARLY_DEPARTURE_MINUTES)
    phase = "upcoming" if now < start else ("in_progress" if now < end else "completed")
    # The class itself must have been watched (start to end, or to now while running)
    covered = _coverage(ctx, period.classroom_id, start, min(end, now))
    day_start = datetime.combine(day, time.min)

    previous_by_student = {r["student_db_id"]: r for r in (previous or {}).get("students", [])}
    previous_end = local_naive(day, datetime.strptime(previous["end_time"], "%H:%M").time()) if previous else None

    students = []
    for student in roster:
        events = period_events(ctx, period.classroom_id, student.id)
        before_start = [e for e in events if e.at < start]
        in_window = [e for e in events if window_start <= e.at < end]
        # Inside at the start = the latest event before the start is an ENTRY (an EXIT
        # during the break before this period means they were not inside)
        inside_at_start = bool(before_start) and before_start[-1].event_type == "ENTRY"
        early_arrival = inside_at_start and before_start[-1].at >= window_start
        first_entry = next((e for e in in_window if e.event_type == "ENTRY" and e.at >= start), None)
        exceptions = []

        if ctx.holiday:
            status, arrived_at = "HOLIDAY", None
        elif phase == "upcoming":
            # Nothing can be decided before the period starts (people inside now may leave)
            status, arrived_at = "NOT_STARTED", None
        elif inside_at_start or (first_entry is not None and first_entry.at <= late_after):
            status = "PRESENT"
            arrived_at = before_start[-1].at if early_arrival else (None if inside_at_start else first_entry.at)
        elif first_entry is not None:
            status, arrived_at = "LATE", first_entry.at
            exceptions.append({"type": "LATE_ARRIVAL",
                               "reason": f"First entry at {first_entry.at:%H:%M}, after {late_after:%H:%M} "
                                         f"(start {start:%H:%M} + {grace} min grace)"})
        elif phase == "upcoming":
            status, arrived_at = "NOT_STARTED", None
        elif not covered:
            status, arrived_at = "UNVERIFIED", None
        elif phase == "in_progress":
            status, arrived_at = "NOT_ARRIVED", None
        else:
            status, arrived_at = "ABSENT", None

        # Departures during the period (only meaningful if the student was there)
        left_at, returned = None, False
        if status in ("PRESENT", "LATE"):
            exits = [e for e in in_window if e.event_type == "EXIT" and e.at >= (arrived_at or window_start)]
            for exit_event in exits:
                came_back = any(e.event_type == "ENTRY" and exit_event.at < e.at < end for e in in_window)
                if came_back:
                    returned = True
                    exceptions.append({"type": "LEFT_DURING_PERIOD",
                                       "reason": f"Left at {exit_event.at:%H:%M} and came back before the end"})
                else:
                    left_at = exit_event.at
            if left_at is not None and left_at < early_departure_before:
                if phase == "completed":
                    exceptions.append({"type": "EARLY_DEPARTURE",
                                       "reason": f"Left at {left_at:%H:%M}, before {early_departure_before:%H:%M} "
                                                 f"(end {end:%H:%M}) and did not return"})
                else:
                    # The period is still running: they may yet come back
                    exceptions.append({"type": "OUT_OF_CLASS",
                                       "reason": f"Left at {left_at:%H:%M} and has not come back yet"})

        if status == "ABSENT":
            prev = previous_by_student.get(student.id)
            exited_after_prev = previous_end is not None and any(
                e.event_type == "EXIT" and e.at >= previous_end - timedelta(minutes=settings.ATTENDANCE_EARLY_DEPARTURE_MINUTES)
                for e in events if e.at < end)
            if prev and prev.get("status") in ATTENDED and exited_after_prev:
                exceptions.append({"type": "DID_NOT_RETURN_AFTER_BREAK",
                                   "reason": "Attended the previous period, left, and has not come back"})
            else:
                exceptions.append({"type": "ABSENT", "reason": "No entry recorded while an entry camera was running"})

        intervals = inside_intervals(events, day_start, min(now, datetime.combine(day, time.max)))
        minutes_inside = round(_overlap_seconds(intervals, start, min(end, now)) / 60.0, 1) if phase != "upcoming" else 0.0

        computed_status = status
        override = ctx.overrides.get((period.id, student.id))
        if override is not None:
            status = override.status

        students.append({
            "student_db_id": student.id,
            "student_id": student.student_id,
            "name": student.name,
            "status": status,
            "computed_status": computed_status,
            "overridden": override is not None,
            "override_reason": override.reason if override is not None else None,
            "arrived_at": _fmt(arrived_at),  # None when already inside at the start
            "inside_at_start": inside_at_start,
            "left_at": _fmt(left_at),
            "returned_during_period": returned,
            "minutes_inside": minutes_inside,
            "exceptions": [] if override is not None and override.status in ("PRESENT", "EXCUSED") else exceptions,
        })

    counts = {s: 0 for s in STATUSES}
    for row in students:
        counts[row["status"]] += 1
    decided = len(students) - counts["UNVERIFIED"] - counts["NOT_STARTED"] - counts["HOLIDAY"]
    attended = sum(counts[s] for s in ATTENDED)
    return {
        "period_id": period.id,
        "date": day.isoformat(),
        "class_name": period.class_name,
        "course_code": period.course_code,
        "course_name": period.course_name,
        "classroom_id": period.classroom_id,
        "start_time": f"{period.start_time:%H:%M}",
        "end_time": f"{period.end_time:%H:%M}",
        "grace_minutes": grace,
        "phase": phase,
        "holiday": ctx.holiday,
        "camera_coverage": covered,
        "roster_size": len(students),
        "counts": counts,
        "decided_count": decided,  # denominator of attendance_rate
        # Denominator: roster minus students whose status cannot be decided
        "attendance_rate": round(attended / decided * 100.0, 1) if decided > 0 else None,
        "students": students,
    }


def periods_on(db: Session, day: date, class_name: Optional[str] = None,
               classroom_id: Optional[int] = None) -> List[models.TimetablePeriod]:
    query = db.query(models.TimetablePeriod).filter(models.TimetablePeriod.day_of_week == day.weekday(),
                                                    models.TimetablePeriod.is_active == True)  # noqa: E712
    if class_name:
        query = query.filter(models.TimetablePeriod.class_name == class_name)
    if classroom_id:
        query = query.filter(models.TimetablePeriod.classroom_id == classroom_id)
    periods = query.order_by(models.TimetablePeriod.class_name, models.TimetablePeriod.start_time).all()
    # A weekly period applies from the local day it was added to the timetable: it must not
    # create (and possibly mark absent) occurrences on earlier dates when it did not exist
    return [p for p in periods if p.created_at is None or stored_to_local_naive(p.created_at).date() <= day]


def compute_day(db: Session, day: date, class_name: Optional[str] = None, classroom_id: Optional[int] = None,
                now: Optional[datetime] = None) -> List[Dict]:
    """All periods of one day, in order, each class's periods chained for the
    DID_NOT_RETURN_AFTER_BREAK check"""
    now = now or datetime.now()
    ctx = load_day(db, day, now)
    rosters: Dict[str, List[models.Student]] = {}
    results, previous_by_class = [], {}
    for period in periods_on(db, day, class_name, classroom_id):
        if period.class_name not in rosters:
            rosters[period.class_name] = roster_for(db, period.class_name)
        result = compute_period(db, period, day, now, ctx, rosters[period.class_name],
                                previous_by_class.get(period.class_name))
        previous_by_class[period.class_name] = result
        results.append(result)
    return results


def daily_summary(db: Session, day: date, class_name: Optional[str] = None, now: Optional[datetime] = None) -> Dict:
    periods = compute_day(db, day, class_name, now=now)
    exceptions = []
    for p in periods:
        for s in p["students"]:
            for e in s["exceptions"]:
                exceptions.append({"date": p["date"], "period_id": p["period_id"], "course_name": p["course_name"],
                                   "class_name": p["class_name"], "start_time": p["start_time"],
                                   "student_db_id": s["student_db_id"], "student_id": s["student_id"],
                                   "name": s["name"], "type": e["type"], "reason": e["reason"],
                                   "basis": "recorded attendance events"})
    return {
        "date": day.isoformat(),
        "periods": [{k: v for k, v in p.items() if k != "students"} for p in periods],
        "exceptions": exceptions,
    }


def monthly_summary(db: Session, year: int, month: int, class_name: Optional[str] = None,
                    now: Optional[datetime] = None) -> Dict:
    """Per student: scheduled periods that are over (holidays excluded) and how each
    ended. attendance_rate = (PRESENT + LATE + EXCUSED) / (decided periods), where
    decided excludes UNVERIFIED periods (no camera running)."""
    now = now or datetime.now()
    first = date(year, month, 1)
    last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    end_day = min(last, now.date())
    per_student: Dict[int, Dict] = {}
    days_with_classes = 0
    day = first
    while day <= end_day:
        periods = [p for p in compute_day(db, day, class_name, now=now) if p["phase"] == "completed" and not p["holiday"]]
        if periods:
            days_with_classes += 1
        for p in periods:
            for s in p["students"]:
                row = per_student.setdefault(s["student_db_id"], {
                    "student_db_id": s["student_db_id"], "student_id": s["student_id"], "name": s["name"],
                    "class_name": p["class_name"], "scheduled": 0,
                    **{status.lower(): 0 for status in ("PRESENT", "LATE", "ABSENT", "EXCUSED", "UNVERIFIED")},
                    "early_departures": 0, "minutes_inside": 0.0})
                row["scheduled"] += 1
                key = s["status"].lower()
                if key in row:
                    row[key] += 1
                row["early_departures"] += sum(1 for e in s["exceptions"] if e["type"] == "EARLY_DEPARTURE")
                row["minutes_inside"] = round(row["minutes_inside"] + s["minutes_inside"], 1)
        day += timedelta(days=1)

    students = sorted(per_student.values(), key=lambda r: (r["class_name"] or "", r["name"]))
    for row in students:
        decided = row["scheduled"] - row["unverified"]
        attended = row["present"] + row["late"] + row["excused"]
        row["attendance_rate"] = round(attended / decided * 100.0, 1) if decided > 0 else None
    return {"year": year, "month": month, "class_name": class_name, "through": end_day.isoformat(),
            "days_with_classes": days_with_classes, "students": students}


def _pct(part: int, whole: int) -> Optional[float]:
    return round(part / whole * 100.0, 2) if whole > 0 else None


def student_course_summary(db: Session, student: models.Student, start_day: date, end_day: date,
                           now: Optional[datetime] = None) -> Dict:
    """One student's attendance per course over [start_day, end_day], from the same
    per-period rules as the staff pages. Each completed period counts as one hour.
      max_hours     = completed periods that could be decided (holidays and UNVERIFIED
                      periods, when no entry camera was running, are left out)
      attended      = PRESENT + LATE;  od_ml = EXCUSED (overrides with a reason)
      average_pct   = attended / max;  od_ml_pct = od_ml / max;  total_pct = both together"""
    now = now or datetime.now()
    end_day = min(end_day, now.date())
    courses: Dict[str, Dict] = {}
    day = start_day
    while day <= end_day and student.class_name:
        periods = periods_on(db, day, student.class_name)
        if periods:
            ctx = load_day(db, day, now)
            previous = None
            for period in periods:
                result = compute_period(db, period, day, now, ctx, [student], previous)
                previous = result
                if result["phase"] != "completed" or result["holiday"]:
                    continue
                key = period.course_code or period.course_name
                row = courses.setdefault(key, {"course_code": period.course_code, "course_name": period.course_name,
                                               "max_hours": 0, "attended": 0, "late": 0, "absent": 0, "od_ml": 0,
                                               "unverified": 0})
                status = result["students"][0]["status"]
                if status == "UNVERIFIED":
                    row["unverified"] += 1
                    continue
                row["max_hours"] += 1
                if status in ("PRESENT", "LATE"):
                    row["attended"] += 1
                    row["late"] += status == "LATE"
                elif status == "EXCUSED":
                    row["od_ml"] += 1
                else:
                    row["absent"] += 1
        day += timedelta(days=1)

    rows = sorted(courses.values(), key=lambda r: (r["course_code"] or "", r["course_name"]))
    for row in rows:
        row["average_pct"] = _pct(row["attended"], row["max_hours"])
        row["od_ml_pct"] = _pct(row["od_ml"], row["max_hours"])
        row["total_pct"] = _pct(row["attended"] + row["od_ml"], row["max_hours"])
    totals = {k: sum(r[k] for r in rows) for k in ("max_hours", "attended", "late", "absent", "od_ml", "unverified")}
    totals["total_pct"] = _pct(totals["attended"] + totals["od_ml"], totals["max_hours"])
    return {
        "student": {"id": student.id, "student_id": student.student_id, "name": student.name,
                    "class_name": student.class_name, "department": student.department},
        "start_date": start_day.isoformat(), "end_date": end_day.isoformat(),
        "courses": rows, "totals": totals,
    }


# --- presence sessions (entry/exit pairs) ------------------------------------------

def presence_sessions(db: Session, start_day: date, end_day: date, student_id: Optional[int] = None,
                      now: Optional[datetime] = None) -> List[Dict]:
    """ENTRY/EXIT pairs per student per local day over an inclusive date range, newest
    first. Unpaired events are reported, never silently dropped:
      open      ENTRY with no EXIT yet (still inside, or exit not seen)
      no_entry  EXIT without a preceding ENTRY that day"""
    now = now or datetime.now()
    query = db.query(models.AttendanceEvent).filter(
        crud.timestamp_at_or_after(models.AttendanceEvent.timestamp, crud.local_day_start_utc(start_day)),
        crud.timestamp_before(models.AttendanceEvent.timestamp, crud.local_day_start_utc(end_day + timedelta(days=1))),
    )
    if student_id is not None:
        query = query.filter(models.AttendanceEvent.student_id == student_id)
    rows = query.all()
    students = {s.id: s for s in db.query(models.Student).filter(
        models.Student.id.in_({r.student_id for r in rows})).all()} if rows else {}
    cameras = {c.id: c.name for c in db.query(models.Camera).all()}

    grouped: Dict[Tuple[int, date], List[models.AttendanceEvent]] = {}
    for row in rows:
        if row.timestamp is None:
            continue
        local = stored_to_local_naive(row.timestamp)
        grouped.setdefault((row.student_id, local.date()), []).append(row)

    sessions = []
    for (sid, day), items in grouped.items():
        items.sort(key=lambda r: (r.timestamp, r.id))
        student = students.get(sid)
        open_entry = None

        def emit(entry, exit_, flag):
            entry_at = stored_to_local_naive(entry.timestamp) if entry else None
            exit_at = stored_to_local_naive(exit_.timestamp) if exit_ else None
            duration = None
            if entry_at and exit_at:
                duration = round((exit_at - entry_at).total_seconds() / 60.0, 1)
            elif entry_at and day == now.date():
                duration = round((now - entry_at).total_seconds() / 60.0, 1)
            sessions.append({
                "student_db_id": sid,
                "student_id": student.student_id if student else None,
                "name": student.name if student else None,
                "class_name": student.class_name if student else None,
                "person_type": student.person_type if student else None,
                "date": day.isoformat(),
                "entry_at": _fmt(entry_at), "exit_at": _fmt(exit_at),
                "entry_camera": cameras.get(entry.camera_id) if entry else None,
                "exit_camera": cameras.get(exit_.camera_id) if exit_ else None,
                "duration_minutes": duration,
                "status": flag,
                "manual": any(r is not None and (r.source or "camera") != "camera" for r in (entry, exit_)),
            })

        for row in items:
            if row.event_type == "ENTRY":
                if open_entry is not None:
                    emit(open_entry, None, "no_exit")  # a second ENTRY before any EXIT
                open_entry = row
            else:
                if open_entry is None:
                    emit(None, row, "no_entry")
                else:
                    emit(open_entry, row, "complete")
                    open_entry = None
        if open_entry is not None:
            emit(open_entry, None, "open" if day == now.date() else "no_exit")

    sessions.sort(key=lambda s: (s["date"], s["entry_at"] or s["exit_at"] or ""), reverse=True)
    return sessions
