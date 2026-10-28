# AttendAI — user guide for the demonstration

A short, practical guide. Detailed camera procedure: [CAMERA_INTEGRATION_RUNBOOK.md](CAMERA_INTEGRATION_RUNBOOK.md).

**Status key** — ✅ implemented and covered by automated tests · 🔶 implemented, still needs testing with
the real cameras · ❌ not available.

## 1. Start the application

Check first whether it is already running (PowerShell, any folder):

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8000,3000 -ErrorAction SilentlyContinue | Select-Object LocalAddress, LocalPort
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json -Depth 3
```
Both ports listed on `127.0.0.1` and `"status": "healthy"` → it is running; skip to step 2.

Otherwise start it in **two** PowerShell windows (keep both open):

```powershell
# Window 1 - backend (always use .venv, never backend\venv)
cd C:\Users\surya\Desktop\AttendAI\attendai\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Window 2 - frontend (ready after about a minute: "Compiled successfully")
cd C:\Users\surya\Desktop\AttendAI\attendai\frontend
$env:HOST='127.0.0.1'; npm start
```

| URL | What |
|---|---|
| http://127.0.0.1:3000 | **The real AttendAI** (use this) |
| http://127.0.0.1:8000/health | Backend health: `schema: up_to_date`, `person_detection_model: ok` |
| http://127.0.0.1:3001 | Separate **DEMO** copy with fictional data (orange "DEMO instance" banner). Never connect cameras to it; stop it before tests (`demo\demo_pids.txt` has the stop command). |

Both services listen on this computer only. For Teams, **share your screen**; do not open the ports to the network.

## 2. Log in

Open http://127.0.0.1:3000 → **Username or email**: `Surya` → **Password**: the one you set in the
terminal → **Sign In**. You land on the **Dashboard**. There is no public sign-up ("Need an account? Ask
an AttendAI administrator."). Sessions last 30 minutes; you are then asked to sign in again.

Forgotten password (sets a new one at a hidden prompt; nothing is shown):
```powershell
cd C:\Users\surya\Desktop\AttendAI\attendai\backend
.\.venv\Scripts\python.exe scripts\manage_users.py set-password Surya
```

## 3. Initial setup (✅)

Left menu: **Operations** (Dashboard, Live Monitoring) · **Attendance** (Attendance, Reports) · **Setup**
(Students, Cameras, Timetable, Users).

1. **Timetable → Classrooms** card: type a name (e.g. `Room 204`) → **Add**.
2. **Timetable → Add period**: **Classroom**, **Class group** (must be *exactly* the students' "Class / grade",
   e.g. `CSE-B`), **Course code**, **Course name**, **Day**, **Start**, **End**, **Grace minutes** (empty = 10)
   → **Save period**. Overlapping periods are refused. A period only counts from the day it was added.
3. **Timetable → Holidays** card: date + name → **Add** (no attendance on those days).
4. **Users → Create account**: username, email, **Role** (Viewer = read-only, Operator = day-to-day incl.
   starting cameras and enrolling faces, Administrator = everything, Student = only their own page + pick the
   **Student record**), initial password twice → **Create account**. Give passwords in person.

## 4. Students and face enrollment

**Add people** (✅): **Students → Add student**: **Student ID** (institution ID), **Full name**,
**Person type** (Student/Staff), **Class / grade**, **Department** → **Add student**.
For 40–60 students use **Students → Import CSV** with `docs\roster_template.csv`
(columns `student_id,name,class_name,department,person_type,is_active`): it checks the file first, then **Import**.

**Enroll faces** (✅ upload and quality checks; 🔶 recognition quality on CCTV) — only with the person's consent:
1. **Students** → the person's row → **Enroll face** → **Choose photo** → **Upload and enroll**.
2. Photo rules: JPEG/PNG, ≤ 5 MB, **exactly one face**, front-facing, face ≥ **64 px**, sharp, not too dark or
   over-exposed. Bad photos are rejected with the reason.
3. If a yellow warning says the face is **similar to another person** or **does not match** this person's
   other photos, stop and check you selected the right person. Use **Enroll anyway** only when you are sure.
4. Add **3–5 photos** per person (straight, slightly turned, with/without glasses), ideally in classroom light.
5. Verify: success message "Face enrolled for …"; the row's **Face** column changes from **Not enrolled** to
   e.g. **3 faces**; **Details** → **Enrolled faces** lists them. Photos are not stored, only encodings;
   **Details → Delete** removes one (e.g. if consent is withdrawn).

## 5. Cameras (🔶 needs the real feeds)

**Add** (Administrator): **Cameras → Add camera** → **Camera name** (e.g. `Room 204 - IN`), **Camera type**
**IN (entrance: records ENTRY)** or **OUT (exit: records EXIT)**, **Source type** **RTSP / IP stream**,
**Source** = the stream URL from IT *including* the viewer username/password, **Width/Height** matching the
camera's shape (16:9 → `1280 × 720`; 4:3 → `640 × 480`), **FPS**, **Classroom** → **Add camera**.
The password is typed **only** into this field. Afterwards the list shows `rtsp://***@…`; when editing, leave
`***` unchanged to keep it.

**Test** (Operator): **Cameras** → row → **Test**. It opens the stream (gives up after 10 s), reads one frame,
closes it and shows "Test: OK" or "failed" with the reason. Nothing is recorded.

**Start/stop** (Operator): **Live Monitoring** → **Camera** (pick one) → **Start** → **Start processing**.
To stop: **Stop** → **Stop processing**. An inactive camera must first be **Activate**d on the Cameras page.
**Deactivate** prevents starting it. Start **one** camera first; never two cameras with the same source.

## 6. Live operation

| Where | What you see | Status |
|---|---|---|
| **Live Monitoring** | Annotated frames of the selected camera; **Status**: Frames processed, Processing rate, **Enrolled faces loaded**, **Students inside now**, **Unregistered inside**, Average processing time, **Last frame**, Reconnects; error messages | 🔶 |
| **Live Monitoring → Recognition log** | Who was identified, by which camera, with a chip **ENTRY** / **EXIT** / **Visitor ENTRY** (recorded) or **Seen** (not recorded); hover the chip for the reason (e.g. still confirming, already inside, cooldown) | 🔶 |
| **Live Monitoring → Recent attendance** | ENTRY/EXIT events as they are recorded | 🔶 |
| **Dashboard** | Present today, Inside now, Unregistered inside, Cameras processing, **Alerts**, **Classroom occupancy**, **Recent events** (**Refresh**) | ✅ logic / 🔶 live |
| **Cameras** | Status chips (Active / Inactive / Processing) and the last Test result | ✅ |

Unknown people are counted only after stable sightings at the IN camera and removed at OUT; the count is in
memory and resets if the backend restarts.

## 7. Attendance, corrections, reports (✅)

- **Attendance → Event log**: every ENTRY/EXIT (manual ones are labelled **manual**).
- **Attendance → Presence sessions**: entry/exit pairs and time inside; **Export CSV** / **Export PDF**.
  Missed by the camera? **Record missed event** → Person, Event, When, **Reason** → **Record event** (Operator; audited).
- **Students → Attendance** (row button): one student's course-wise %, hours and table.
- **Reports → Report type**: **Period attendance** (per period; **Override** a student's status with a
  reason → **Save override**, **Clear** to undo; Operator, audited), **Daily summary**, **Monthly summary**;
  **Export CSV** / **Export PDF**.
- **Users → Audit log**: who changed what (accounts, cameras, overrides, manual events). Passwords never appear.

## 8. Tomorrow's first test (checklist)

1. Before: back up the database **with the backend stopped**:
   `Copy-Item backend\data\attendai.db backend\data\attendai.db.bak-<date>-pre-cameras`, then start again.
2. Get from IT: camera model, stream URL format, a **read-only** camera account (handed over in person),
   which camera is **IN** and which **OUT**. Check reachability: `Test-NetConnection <camera-ip> -Port 554`.
3. Enter the timetable period for the test and enroll the **consenting** participants (3–5 photos each).
4. Add the **IN** camera → **Test** → **Live Monitoring → Start**. Check **Last frame** < 2 s and
   **Enrolled faces loaded**. One enrolled person walks in slowly, facing the camera → **Recognition log**
   shows **Seen** (confirming) and then **ENTRY**. Stop it.
5. Same for the **OUT** camera (an EXIT needs an ENTRY earlier the same day).
6. Start **both**. Run the scenarios: single entries, a group of 3–5, simultaneous IN/OUT, 3–5 **unregistered**
   volunteers (should appear only as unknown / in **Unregistered inside**).
7. Ground truth: an observer writes one row per real crossing in `eval\ground_truth.csv`
   (`person,event_type,timestamp,notes`; visitors as `VISITOR-1`, …). Create templates with
   `.\.venv\Scripts\python.exe scripts\evaluate_accuracy.py --write-templates eval` (from `backend`).
8. Score (read-only, counts **camera events only**; do manual corrections *after* scoring):
   `.\.venv\Scripts\python.exe scripts\evaluate_accuracy.py --db data\attendai.db --ground-truth eval\ground_truth.csv --start <ISO time> --end <ISO time> --out eval\report`
   Accuracy = matched ÷ (enrolled crossings + wrong identity + spurious). Report it with the conditions.

## 9. Known limitations (say these before the demo)

- **Accuracy is not yet measured** on real cameras. Unit tests and the DEMO copy do not prove accuracy.
- **5-sighting confirmation**: an ENTRY/EXIT needs the same face recognised in **5 consecutive processed
  frames** — roughly **2–5 seconds** facing the camera on this CPU. People walking quickly past may be missed.
- **Strict matching**: by default only very close matches count (distance ≤ 0.30). This avoids wrong names
  but causes more misses on CCTV; calibrate first (runbook §5), do not change it blindly.
- **No liveness / anti-spoofing** ❌: a printed photo or phone screen of an enrolled student may be accepted.
- Frontal faces only; small, distant, blurred, back-lit or masked faces are often not detected.
- An EXIT is only recorded if the person's ENTRY was recognised that day.
- Many faces at once slow processing (≈ 0.3–0.6 s per face). The unregistered count resets on restart.

## 10. Troubleshooting

| Problem | Safe remedy |
|---|---|
| Page does not load | Check step 1; start the missing window. Wait for "Compiled successfully". |
| Dashboard chip says "API offline" | Backend window closed or crashed → start it again (step 1). |
| "Database needs upgrade" / health 503 | Do not continue; back up the database and ask before migrating. |
| "Incorrect username or password" | Check spelling/caps; reset with `manage_users.py set-password Surya` (step 2). |
| Signed out suddenly | The 30-minute session expired; sign in again. |
| **Test** failed | `Test-NetConnection <ip> -Port 554`; check the URL path (main/sub stream), the camera account, then IT's firewall. Never paste the password into chat or files. |
| Camera state disconnected / reconnecting | It retries automatically; check network/power. **Stop** it if it keeps failing. The other camera keeps working. |
| **Start** is greyed out | Camera inactive (**Cameras → Activate**), already running, or you are not an Operator/Administrator. |
| Feed runs but nobody is recognised | **Enrolled faces loaded** = 0? Enroll faces. Faces too small/angled → move camera closer, more light; see runbook §5. |
| Wrong person recorded | **Record missed event** / **Override** with a reason; never edit the database. |
| Attendance shows UNVERIFIED | No IN camera was running during that period, so absence cannot be decided. |
| Student not on the period's list | Their **Class / grade** must exactly match the period's **Class group**. |
| Things go badly wrong | **Stop** both cameras; restore the backup with the backend stopped (runbook §10). |
