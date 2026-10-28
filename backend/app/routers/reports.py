"""
Report exports. Every report is computed from the same functions the API and the UI use
(app/attendance_logic.py), so a CSV/PDF always matches what the screens show.

CSV cells that a spreadsheet would treat as a formula (=, +, -, @, tab, CR) are prefixed
with an apostrophe (CSV injection protection).
"""
import csv
import io
from datetime import date, datetime
from typing import Iterable, List, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import attendance_logic, models
from app.database import get_db
from app.permissions import require_viewer
from app.routers.attendance import check_range

router = APIRouter()

PDF_ROWS_PER_PAGE = 28


def _safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def csv_response(filename: str, header: Sequence[str], rows: Iterable[Sequence]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_safe(v) for v in row])
    # UTF-8 BOM so Excel opens non-ASCII names correctly
    return Response(content="﻿" + buffer.getvalue(), media_type="text/csv",  # Starlette appends "; charset=utf-8" to text/* types
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def pdf_response(filename: str, title: str, subtitle: str, header: Sequence[str], rows: List[Sequence],
                 notes: Optional[List[str]] = None) -> Response:
    """Simple paginated table PDF (matplotlib, no extra dependency)"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
    except Exception:
        raise HTTPException(status_code=503, detail="PDF export needs matplotlib, which is not installed; use CSV")

    buffer = io.BytesIO()
    pages = [rows[i:i + PDF_ROWS_PER_PAGE] for i in range(0, len(rows), PDF_ROWS_PER_PAGE)] or [[]]
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    with PdfPages(buffer, metadata={"Title": title, "Creator": "AttendAI"}) as pdf:
        for number, page_rows in enumerate(pages, start=1):
            fig = plt.figure(figsize=(11.69, 8.27))  # A4 landscape
            fig.text(0.04, 0.95, title, fontsize=15, weight="bold")
            fig.text(0.04, 0.92, subtitle, fontsize=9, color="#444444")
            fig.text(0.96, 0.03, f"Page {number} of {len(pages)} · generated {generated} · AttendAI",
                     fontsize=7, color="#666666", ha="right")
            if notes and number == len(pages):
                for i, note in enumerate(notes):
                    fig.text(0.04, 0.06 - i * 0.015, note, fontsize=7, color="#444444")
            ax = fig.add_axes([0.04, 0.1, 0.92, 0.78])
            ax.axis("off")
            if page_rows:
                table = ax.table(cellText=[[str(v if v is not None else "") for v in r] for r in page_rows],
                                 colLabels=list(header), loc="upper left", cellLoc="left", colLoc="left")
                table.auto_set_font_size(False)
                table.set_fontsize(7.5)
                table.scale(1, 1.25)
                for (row_index, _), cell in table.get_celld().items():
                    cell.set_edgecolor("#d0d5dd")
                    if row_index == 0:
                        cell.set_facecolor("#eef2f6")
                        cell.set_text_props(weight="bold")
            else:
                ax.text(0, 0.95, "No data for this selection.", fontsize=10)
            pdf.savefig(fig)
            plt.close(fig)
    return Response(content=buffer.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --- attendance records ---------------------------------------------------------------

SESSION_HEADER = ("Date", "ID", "Name", "Type", "Class", "Entry", "Exit", "Minutes inside", "Status",
                  "Entry camera", "Exit camera", "Manual")


def _session_rows(sessions):
    for s in sessions:
        yield (s["date"], s["student_id"], s["name"], s["person_type"], s["class_name"],
               (s["entry_at"] or "")[11:16], (s["exit_at"] or "")[11:16], s["duration_minutes"], s["status"],
               s["entry_camera"], s["exit_camera"], "yes" if s["manual"] else "")


@router.get("/sessions.csv")
async def sessions_csv(start_date: date, end_date: date, db: Session = Depends(get_db),
                       current_user=Depends(require_viewer)):
    check_range(start_date, end_date)
    sessions = attendance_logic.presence_sessions(db, start_date, end_date)
    return csv_response(f"attendance_{start_date}_{end_date}.csv", SESSION_HEADER, _session_rows(sessions))


@router.get("/sessions.pdf")
async def sessions_pdf(start_date: date, end_date: date, db: Session = Depends(get_db),
                       current_user=Depends(require_viewer)):
    check_range(start_date, end_date)
    sessions = attendance_logic.presence_sessions(db, start_date, end_date)
    return pdf_response(f"attendance_{start_date}_{end_date}.pdf", "Attendance record",
                        f"{start_date:%d %b %Y} - {end_date:%d %b %Y} · {len(sessions)} entry/exit sessions",
                        SESSION_HEADER, list(_session_rows(sessions)),
                        ["Status: complete = entry and exit; open = still inside; no_exit / no_entry = an event "
                         "was not paired (camera missed it or manual correction needed)."])


# --- period, daily, monthly -------------------------------------------------------------

@router.get("/period.csv")
async def period_csv(period_id: int, day: date = Query(..., alias="date"), db: Session = Depends(get_db),
                     current_user=Depends(require_viewer)):
    period = db.query(models.TimetablePeriod).filter(models.TimetablePeriod.id == period_id).first()
    if period is None:
        raise HTTPException(status_code=404, detail="Period not found")
    if day.weekday() != period.day_of_week:
        raise HTTPException(status_code=400, detail="The period is not scheduled on that date")
    result = next((r for r in attendance_logic.compute_day(db, day, class_name=period.class_name)
                   if r["period_id"] == period_id), None) or attendance_logic.compute_period(db, period, day)
    rows = [(s["student_id"], s["name"], s["status"], s["computed_status"] if s["overridden"] else "",
             s["override_reason"] or "", (s["arrived_at"] or "")[11:16], (s["left_at"] or "")[11:16],
             s["minutes_inside"], "; ".join(f"{e['type']}: {e['reason']}" for e in s["exceptions"]))
            for s in result["students"]]
    return csv_response(f"period_{period_id}_{day}.csv",
                        ("ID", "Name", "Status", "Computed status", "Override reason", "Arrived", "Left",
                         "Minutes inside", "Exceptions"), rows)


def _daily_rows(summary):
    for p in summary["periods"]:
        c = p["counts"]
        yield (p["start_time"], p["end_time"], p["class_name"], p["course_name"], p["roster_size"], c["PRESENT"],
               c["LATE"], c["ABSENT"], c["EXCUSED"], c["UNVERIFIED"] + c["NOT_ARRIVED"] + c["NOT_STARTED"],
               "" if p["attendance_rate"] is None else f"{p['attendance_rate']}%",
               "yes" if p["camera_coverage"] else "NO")


DAILY_HEADER = ("Start", "End", "Class", "Course", "Roster", "Present", "Late", "Absent", "Excused", "Undecided",
                "Rate", "Camera coverage")


@router.get("/daily.csv")
async def daily_csv(day: date = Query(..., alias="date"), class_name: Optional[str] = None,
                    db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    summary = attendance_logic.daily_summary(db, day, class_name)
    return csv_response(f"daily_{day}.csv", DAILY_HEADER, _daily_rows(summary))


@router.get("/daily.pdf")
async def daily_pdf(day: date = Query(..., alias="date"), class_name: Optional[str] = None,
                    db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    summary = attendance_logic.daily_summary(db, day, class_name)
    rows = list(_daily_rows(summary))
    exceptions = [(e["start_time"], e["class_name"], e["student_id"], e["name"], e["type"], e["reason"])
                  for e in summary["exceptions"]]
    notes = ["Rate = (present + late + excused) / roster, excluding undecided students (no camera coverage, "
             "or the period has not finished).", f"{len(exceptions)} attendance exception(s) - see the daily CSV "
             "or the Reports page for details."]
    return pdf_response(f"daily_{day}.pdf", f"Daily attendance - {day:%A %d %B %Y}",
                        f"{len(rows)} scheduled period(s){' · class ' + class_name if class_name else ''}",
                        DAILY_HEADER, rows, notes)


MONTHLY_HEADER = ("ID", "Name", "Class", "Periods", "Present", "Late", "Absent", "Excused", "Unverified",
                  "Early departures", "Hours inside", "Rate")


def _monthly_rows(summary):
    for s in summary["students"]:
        yield (s["student_id"], s["name"], s["class_name"], s["scheduled"], s["present"], s["late"], s["absent"],
               s["excused"], s["unverified"], s["early_departures"], round(s["minutes_inside"] / 60.0, 1),
               "" if s["attendance_rate"] is None else f"{s['attendance_rate']}%")


@router.get("/monthly.csv")
async def monthly_csv(year: int = Query(..., ge=2000, le=2100), month: int = Query(..., ge=1, le=12),
                      class_name: Optional[str] = None, db: Session = Depends(get_db),
                      current_user=Depends(require_viewer)):
    summary = attendance_logic.monthly_summary(db, year, month, class_name)
    return csv_response(f"monthly_{year}-{month:02d}.csv", MONTHLY_HEADER, _monthly_rows(summary))


@router.get("/monthly.pdf")
async def monthly_pdf(year: int = Query(..., ge=2000, le=2100), month: int = Query(..., ge=1, le=12),
                      class_name: Optional[str] = None, db: Session = Depends(get_db),
                      current_user=Depends(require_viewer)):
    summary = attendance_logic.monthly_summary(db, year, month, class_name)
    title = f"Monthly attendance - {date(year, month, 1):%B %Y}"
    subtitle = (f"Completed periods through {summary['through']} · {summary['days_with_classes']} day(s) with classes"
                f"{' · class ' + class_name if class_name else ''}")
    return pdf_response(f"monthly_{year}-{month:02d}.pdf", title, subtitle, MONTHLY_HEADER,
                        list(_monthly_rows(summary)),
                        ["Rate = (present + late + excused) / (periods - unverified). Unverified = no entry camera "
                         "was running, so attendance could not be decided."])
