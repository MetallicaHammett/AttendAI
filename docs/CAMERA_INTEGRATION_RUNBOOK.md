# Camera integration and first-test runbook (Phase I)

For the installation of the two classroom cameras (IN = entry, OUT = exit) and the first
controlled tests. Nothing here assumes a camera vendor or protocol: AttendAI accepts a local
device index (webcam/USB/capture card), an `rtsp://` / `rtsps://` stream, an `http(s)://` stream
OpenCV can read (e.g. MJPEG), or a recorded video file in `backend\data\videos`.

**Golden rules**
- Camera passwords are typed **only** into the Cameras page (admin) on this server. Never into
  chat, e-mail, source code, `.env`, screenshots, reports or the terminal. AttendAI shows and logs
  them as `rtsp://***@host/...`. They are stored in the local database file
  (`backend\data\attendai.db`), so that file and its backups are sensitive.
- Test each camera alone before running both. Never run two cameras on the same source.
- Accuracy is **not known** until the ground-truth session below has been scored. Do not quote a
  percentage from unit tests, the demo instance or a dry run.

## 1. Before the installation: what to ask the IT team / SPOC

| Item | Why |
|---|---|
| Camera make and model, firmware | Stream URL format, codec, supported transports |
| Stream protocol and URL **format** (e.g. `rtsp://<host>:554/<path>`), main vs sub-stream path | Sub-stream (lower resolution) is often enough and much cheaper to process |
| Resolution, frame rate, codec (H.264 preferred; H.265 may need a recent FFmpeg) | Processing cost; face size |
| Which physical camera is **IN** and which is **OUT** (label both) | Wrong roles invert entry/exit |
| IP address reachability from this server (same VLAN / firewall rule / port 554 or vendor port) | The server must reach the camera; nothing reaches out to the internet |
| A **read-only viewer account** for the stream, and how the password will be handed over (in person / password manager / sealed note) | Least privilege; no passwords in chat |
| Mounting height, angle, distance to the door, lighting, sun glare at times of day | Faces must be roughly frontal and ≥ 40 px (≥ 20 px with `FACE_DETECTION_UPSAMPLE=2`) |
| Whether the camera has privacy masking / on-camera analytics to disable | Avoid double processing and on-camera cloud features |
| Who authorises recording any test footage, and the retention period | Consent and data minimisation |

## 2. Reachability check without exposing credentials

1. Ping / port check from the server (no password involved):
   `Test-NetConnection <camera-ip> -Port 554` (or the vendor's port). Expect `TcpTestSucceeded : True`.
2. In AttendAI → **Cameras** → **Add camera** (admin): name (`Classroom 204 - IN`), source type
   **RTSP / stream**, the full stream URL **including** the viewer credentials, camera type
   **IN** (or **OUT**), classroom, and a working size that keeps the camera's aspect ratio
   (e.g. `1280 × 720` for 16:9; `640 × 480` only for 4:3 cameras). Save. The list now shows
   `rtsp://***@<host>/...`.
3. Click **Test** on that camera. It opens the stream (≤ 10 s), reads one frame, releases it and
   reports the frame size. Nothing is recorded. If it fails, check in order: network/firewall, URL
   path (main vs sub stream), credentials, codec (try the sub-stream), then `OPENCV_FFMPEG_CAPTURE_OPTIONS`
   (`rtsp_transport;udp` only if the camera refuses TCP).

## 3. One camera at a time, then both

1. Start **IN** only: **Live Monitoring** → choose it in **Camera** → **Start** → **Start processing**.
   In the **Status** panel check: **Processing** chip, **Last frame** under 2 s ago, **Average
   processing time**, **Enrolled faces loaded** = your enrolled count, and no error message. (The
   number of old frames skipped to stay current, `frames_skipped`, is only in the API status
   `GET /video/{camera_id}/status`; non-zero is normal.)
2. Walk through the door yourself (enrolled operator, consenting). Watch the **Recognition log**:
   `STABILIZING` → `ENTRY`. If faces are not detected: move the camera closer / lower, use the
   main stream, or try `FACE_DETECTION_UPSAMPLE=2` (slower; measure).
3. Stop IN, repeat for **OUT** (requires an active ENTRY today for an EXIT).
4. Start both. Confirm both are `running` and the dashboard updates.

## 4. Roster and consent-aware enrollment

- Roster fields (CSV, Students → Import; template `docs\roster_template.csv`): `student_id`
  (institution ID), `name`, `class_name` (must match the timetable class), `department`,
  `person_type` (`student`/`staff`). Dry-run first, then import.
- **Consent first**: enroll only people who have agreed (written or as the institution requires);
  record who consented and when outside AttendAI. Anyone may withdraw: delete their face records
  (Students → Details → face records) - encodings are removed; no photos are stored.
- Enrollment (Students → Enroll face, operator): 3–5 photos per person, frontal and slightly
  turned, with and without glasses if worn, under the classroom's lighting. The app rejects photos
  with a face < 64 px, blurred (sharpness < 30) or too dark/bright, and warns when a photo looks
  like **another** enrolled person or unlike the person's own photos - stop and check identity.
- Photos are processed in memory and discarded; only 128-number encodings are stored.

## 5. Calibration before the evaluation (recommended)

The default `ATTENDANCE_MIN_CONFIDENCE=70` accepts only matches within distance **0.30**, much
stricter than dlib's 0.6 reference. CCTV images of the same person often score 0.35–0.50, so the
default may produce many missed recognitions. With consent, record a short clip at the door
(5–10 enrolled volunteers, 2–3 non-enrolled), place it in `backend\data\videos`, add a **video
file** camera, and replay it at 70, 60 and 50 (set in `backend\.env`, restart the backend; never
lower `FACE_MATCH_TOLERANCE` above 0.6). Pick the value with the best balance of missed vs wrong
identities on that clip; delete the clip afterwards unless retention is authorised.

## 6. Ground-truth evaluation (the ≥ 50 % requirement)

Measurement method (implemented in `scripts\evaluate_accuracy.py`; see TESTING_AND_EVALUATION.md):
- **Population**: the enrolled class (40–60) plus 3–5 non-enrolled volunteers (`VISITOR-n`).
- **Ground truth**: a human observer writes one row per real door crossing
  (`person,event_type,timestamp,notes`) - e.g. from a stopwatch log or by reviewing a consented
  recording. Each crossing is one case, never one per frame.
- **Correct** = a system event for the same person and type within ±tolerance (default 10 s).
- **Errors**: missed (enrolled crossing with no event), wrong identity (event for a different
  person at that time), spurious (event with no crossing, including a visitor recorded as a student).
- **Denominator**: identification accuracy = matched / (enrolled crossings + wrong identity + spurious).
  Nothing is excluded: difficult crossings, groups, poor light and visitors all stay in.
  The report also states false negatives (= missed), false positives (= wrong identity + spurious,
  including visitors recorded as students), visitor crossings correctly not recorded, recall,
  precision, timing error and the unregistered-count error from the occupancy log.
- **Only camera events are scored.** Manual events (operator corrections, admin edits) are excluded
  and their number is printed, so correcting a missed student during the session never turns the
  miss into a match. Avoid corrections during the scored window; make them afterwards.
- Keep the configured thresholds for the scored session unless the calibration in §5 produced
  evidence for a change, and report the values used next to the result.
- Run: `.\.venv\Scripts\python.exe scripts\evaluate_accuracy.py --db data\attendai.db --ground-truth eval\ground_truth.csv --start <ISO> --end <ISO> --out eval\report`
  (read-only on the database). Report the conditions (lighting, group size, angle) next to the numbers.
- Scenarios to label separately: single file entry, groups of 3–5, simultaneous IN and OUT, low
  light / backlight, caps/masks, visitors, and **photo spoofing** (expect failure: AttendAI has no
  liveness detection; record the outcome honestly).

## 7. Limitations to state before any test or demonstration

- **Confirmation delay.** An ENTRY/EXIT is recorded only after the same person is recognised in
  `ATTENDANCE_MIN_CONSECUTIVE_FRAMES` (default **5**) consecutive processed frames, with gaps of at
  most 2 s (widened automatically for slow frames, max 15 s). With CPU processing of roughly
  0.3–0.6 s per face, that means a person must be visible and recognised for about **2–5 seconds**;
  someone walking straight past may not be recorded. This favours missed events over wrong ones.
- **No liveness / anti-spoofing.** AttendAI cannot tell a live face from a printed photo, a phone or
  a screen. A photo of an enrolled student may be recorded as that student. Do not claim spoofing
  resistance; test it and report the outcome.
- **Strict default threshold** (distance ≤ 0.30, see §5) and **frontal faces only** (HOG detector).
- Unregistered-person counts are kept in memory and reset when the backend restarts.

## 8. Expected behaviour (what is correct)

| Situation | Expected |
|---|---|
| Unregistered person | Shown as UNKNOWN; never an attendance event; counted in "Unregistered inside" after stable sightings at IN, removed at OUT (in memory, resets on restart) |
| Look-alike / unclear match | AMBIGUOUS: neither a student event nor an unregistered count |
| Same student seen for many frames | One ENTRY; repeats show `NO_CHANGE` (already inside / cooldown) |
| Several people together | Each face handled separately; slower frames on CPU |
| Exit without a recognised entry | No EXIT recorded (`OUT camera: no active entry today`); fix with a manual event if needed |
| Poor light / small faces | Fewer detections → missed events, not wrong ones (by design the thresholds favour misses) |
| Stream drops | State `disconnected` → `reconnecting` with backoff; period attendance for uncovered time is UNVERIFIED, not absent |
| Wrong identity recorded | Correct it: Attendance → manual event / period override (operator, reason required, audited) |

## 9. Evidence without over-retaining footage

Keep: the ground-truth CSV, the evaluation report, screenshots of the dashboard/live page with no
credentials visible, and the audit log export. Do not keep raw footage unless the institution
authorises it; AttendAI itself never stores frames (the live view is in memory only).

## 10. Rollback / recovery

| Problem | Action |
|---|---|
| A camera misbehaves | Cameras → Stop (or toggle inactive). Other camera keeps running |
| Backend unstable | Stop the backend window; start it again (§10). Attendance already recorded is in the database |
| Wrong events recorded during testing | Delete/correct them as admin (audited), or restore the pre-test backup |
| Before any test day | Back up: `Copy-Item backend\data\attendai.db backend\data\attendai.db.bak-<date>-pre-cameras` **with the backend stopped** (or use SQLite's backup API) |
| Restore | Stop the backend, copy the backup over `attendai.db`, start the backend, check `/health` |

## 11. Start, URLs, login

```powershell
# backend (from attendai\backend) - loopback only
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# frontend (from attendai\frontend)
$env:HOST='127.0.0.1'; npm start          # http://127.0.0.1:3000
```
- App: http://127.0.0.1:3000 - sign in with your admin account (`Surya`). Forgotten password:
  `.\.venv\Scripts\python.exe scripts\manage_users.py set-password Surya` (interactive, hidden prompt).
- Health: http://127.0.0.1:8000/health (`schema: up_to_date`, `person_detection_model: ok`).
- The DEMO instance (127.0.0.1:3001 / 8001, `demo\start_demo.ps1`) is separate fictional data -
  never connect real cameras to it and stop it before the evaluation to free CPU.
- Use `backend\.venv` (Python 3.12). Do not use `backend\venv`.
- Only expose the service beyond this computer (for the Teams demo, share the screen instead)
  after adding HTTPS and sign-in rate limiting.
