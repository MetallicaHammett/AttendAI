# Operations guide

Roles: **viewer** (read-only), **operator** (people, enrollment, cameras on/off, corrections), **admin**
(cameras, timetable, accounts, deletions). Buttons a role cannot use are hidden, and the server rejects the
request anyway.

## 1. Set up a classroom

1. **Timetable → Classrooms**: add the room (admin).
2. **Cameras → Add camera** (admin): one **IN** camera at the entrance, one **OUT** camera at the exit, both
   assigned to the classroom.
3. **Timetable → Add period** (admin): course, class group (e.g. `CSE-A`), weekday, start/end, optional grace
   minutes. Overlapping periods for the same class or room are rejected. A period counts from the day it is added:
   reports never create attendance for earlier weeks.
4. **Timetable → Holidays**: dates without classes.

### Camera sources

| Type | Source value | Notes |
|---|---|---|
| Webcam | device index, `0`, `1` … | The server's own USB/built-in camera. Two camera records must not use the same index at the same time (the server refuses). |
| RTSP / IP | `rtsp://user:password@192.168.1.20:554/stream1` | The password is stored on the server and always shown as `rtsp://***@…`. When editing, leave `***` unchanged to keep it. |
| Video file | file name inside `backend\data\videos\` | Labelled **Recorded video** everywhere, never "live". |

**Test** (operator) opens the source, reads one frame and releases it. For a webcam it first asks you to
confirm the device number, because the test switches that camera on.

Mount cameras at face height looking along the walking direction, with even front lighting. One person at a time
through a door gives the best results; groups are handled, but faces must be visible to be identified.

## 2. Enroll people

**Students → Add student** or **Import CSV** (operator).

### Roster CSV

Template: **Students → Import CSV → Download the template** (also `docs/roster_template.csv`).

```csv
student_id,name,class_name,department,person_type,is_active
21CSE001,Asha Raman,CSE-A,Computer Science,student,true
STAFF-001,Dr. K. Rao,,Computer Science,staff,true
```

- `student_id` and `name` required; IDs must be unique in the file and not already used (also by deleted records).
- `person_type`: `student` (default) or `staff`. Staff are recognised and logged but never on class rosters.
- `class_name` must match the timetable's class group for period attendance.
- The file is validated first and nothing is saved unless every row is valid; problems are listed by line.

### Face photos

**Enroll face** on a person's row. One clear, front-facing photo with exactly one face, JPEG/PNG ≤ 5 MB.
Add 2–4 photos per person (different days or lighting; with and without glasses) for more robust recognition.

The server rejects photos where the face is smaller than 64 px, blurred, too dark or over-exposed, and warns
(needs "Enroll anyway") when the face looks like another enrolled person or unlike the person's earlier photos —
usually a sign the wrong person was selected. Only a 128-number encoding is stored, never the photo.

**Details** on a person shows their face records (erase permanently) and the last 30 days of entries and exits.

## 3. Run cameras

**Live Monitoring**: choose a camera, **Start**, confirm. Processing runs on the server until **Stop**, even if
the browser is closed. The page shows:

- the annotated frame (green = recognised, yellow = low match score, orange = ambiguous, red = unregistered),
- feed type (Live feed / Recorded video / Synthetic test frames) and camera state (reconnecting, disconnected,
  video ended, stale),
- registered and unregistered people inside, and a recognition log from all cameras.

A dropped live camera is reopened automatically with increasing delays. Recognition problems (e.g. a missing
model file) are shown separately from camera problems.

## 4. Attendance and corrections

- **Attendance → Event log**: every ENTRY/EXIT, newest first, with camera and match score.
- **Attendance → Presence sessions**: entry/exit pairs per person per day with time inside. "No exit recorded" /
  "No entry recorded" mean a camera missed a crossing.
- **Record missed event** (operator): adds a manual ENTRY/EXIT with a mandatory reason; marked "manual" and
  audited.
- **Reports → Period attendance**: status of each rostered student for one period on one date. **Override**
  (operator) sets PRESENT / LATE / ABSENT / EXCUSED with a reason; the computed status stays visible and the
  change is audited.

### How period status is decided

| Status | Rule |
|---|---|
| Present | inside at the start, or first entry ≤ start + grace |
| Late | first entry after start + grace, before the end |
| Absent | no entry, period finished, and the classroom's IN camera delivered frames for the whole class (gaps of at most `ATTENDANCE_MAX_COVERAGE_GAP_MINUTES`, default 5) |
| Unverified | no entry and the camera **did not cover the class** (not running, stream outage, stalled feed, started late) — the system cannot know, so it never says absent |

Camera coverage is recorded only while frames actually arrive: when a stream drops or stalls, its coverage
record is closed at the last frame and a new one starts when frames return. For a classroom with its own
cameras, only those cameras' events (plus manual corrections) count for its periods; a sighting in another
classroom or at a gate never counts as attending this class. Attendance state (inside / cooldowns) is kept
per classroom, so a student recorded in one room can still be recorded by another room's camera the same day.
| Not arrived / Not started | period in progress / not yet started |
| Excused | only by override |

Exceptions: late arrival, absent, left early (period over), out of class now (period running), left and returned,
did not return after the break (attended the previous period, left, missed this one). Attendance rate =
(present + late + excused) ÷ decided students.

## 5. Reports

**Reports → Daily summary / Monthly summary**, and **Attendance → Presence sessions**: CSV (opens in Excel) and
PDF exports, computed by the same code as the screens. Spreadsheet formulas in names are neutralised.

## 6. Accounts

**Users** (admin): change roles, deactivate accounts (takes effect immediately), and read the audit log.
Console alternative: `backend\scripts\manage_users.py`.

### Professor portal (class registers)

1. **Users → Create account**, role **Professor**. A professor account can use only the staff portal: no
   dashboard, cameras, students, reports or accounts.
2. **Timetable → Edit** a period → **Professor**. Only active professor accounts can be chosen, and one
   professor cannot be booked for two overlapping periods.
3. The professor signs in and lands on **Weekly Timetable** (`/staff/timetable`): their classes by day,
   with Needs review / In progress / Upcoming / Submitted / Holiday. **Take attendance** opens the register.

How the register uses camera evidence (`backend/app/routers/staff_portal.py`):

- The roster is the class group's active students. Each row shows the **AI detection** (a suggestion) next
  to the **final status** (the professor's decision).
- Only an entry recognised by an IN camera **assigned to that period's classroom** counts as evidence. An
  entry seen by any other camera (e.g. the main gate) shows only that the student was in the building:
  the row stays *Unverified* ("Seen elsewhere only").
- A student the cameras did not recognise is **never** marked absent automatically: *Unverified* until the
  professor decides. Missing coverage (no classroom camera, camera not running, camera stopped sending
  heartbeats) is shown at the top of the register and on the affected rows.
- **Save Attendance** needs a decision for every student. A reason is required to excuse a student, to
  mark absent a student the classroom camera recorded, and to change a saved decision. Each save creates
  a new register version; a save from an out-of-date copy (second tab, double click, someone else's
  change) is refused and the unsaved decisions stay on screen.
- Decisions are stored as attendance overrides, so reports and the student portal use them. Every save
  is in the audit log (`attendance.register_submit` / `attendance.register_update`) with each change,
  the AI status, and the reason.
- Administrators see every professor's registers under **Class Registers** and can save them too.
  Operators keep the existing per-student override on the Reports pages.

Limits: a per-date cancellation of one class is not supported (deactivate the period, or add a holiday).
The register's evidence rules apply in the portal only; other reports still use the period rules in
section 4.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Top bar "Database needs upgrade" / `/health` schema `outdated` | Run the database upgrade ([SETUP_WINDOWS.md](SETUP_WINDOWS.md#upgrading-the-database)). |
| "API online · model missing", camera start 503 "Person-detection model not found" | Copy `yolov8n.pt` to `backend\` or set `YOLO_MODEL_PATH`. |
| Camera start 503 "could not be opened" | Device unplugged/in use by another app (close Teams/Camera app for webcams), wrong index, wrong RTSP URL/credentials, or firewall. Use **Test** first. |
| Camera start 409 "same source" | Another camera record is already processing that device; stop it. |
| "Reconnecting" for a long time | Network/stream down; check the camera's power and network. Attempts continue every ≤ 30 s. |
| "No new frame for N seconds" | The source stalled; usually followed by reconnecting. |
| People not recognised | Not enrolled (Students page warns), face too small/side-on, poor light; add more photos; check camera placement. |
| Many AMBIGUOUS faces | Look-alike enrolled people or poor photos; re-enroll with better photos; review `FACE_MATCH_AMBIGUITY_MARGIN`. |
| Everyone "Unverified" | No IN camera of the period's classroom was running; assign cameras to the classroom. |
| `face_recognition could not be imported` (503 on enrollment) | `setuptools` too new for `face_recognition_models`; reinstall with `requirements.txt` (pins `setuptools<81`). |
| Port 8000/3000 in use | Another instance is running; stop it or use `--port`/`PORT`. |
| Login fails after restart in development | No `SECRET_KEY` configured: tokens are per-process. Set one in `backend\.env`. |
