# Changelog

## 1.0.0 — 20 Oct 2026

Requires the database migration `e7b3c1a9d2f0` (additive; see docs/SETUP_WINDOWS.md).

### Recognition and occupancy
- Explicit KNOWN / UNKNOWN / AMBIGUOUS face classification (`classify_encoding`); ambiguous faces are never
  counted as unregistered visitors.
- `UnknownOccupancyTracker` integrated into every camera: unregistered people counted once at IN, removed at
  OUT; nearest-only resolution when a student is recognised; drift guard; forward-only day rollover; validated
  settings (Step 1.1).
- Tracking runs on every frame, also when no face is visible.
- Consecutive-sighting gap adapts to slow frames (many faces on CPU).
- Recognition log (throttled, no biometric data) for the live view; names on annotated frames.

### Cameras
- Reconnect with exponential backoff, end-of-file detection, stale-feed detection, processing-time metrics.
- Feed labels live / recorded / synthetic everywhere.
- Credentials masked in API responses, logs, errors and audit; kept on edit.
- Source validation (webcam index, stream URL, video files only inside `VIDEO_FILES_DIR`, stored relative).
- Connection test with explicit device confirmation for webcams; one session per physical source.
- Missing model file is a clear setup error, never a download; Ultralytics runs offline (no analytics).
- Camera runs recorded for attendance coverage; closed on stop, shutdown and after a crash.

### Attendance and reports
- Classrooms, weekly timetable, holidays; period status PRESENT / LATE / ABSENT / UNVERIFIED / NOT_ARRIVED /
  NOT_STARTED / EXCUSED / HOLIDAY with exceptions and reasons.
- Audited manual ENTRY/EXIT events and per-period overrides.
- Presence sessions (entry/exit pairs, time inside), inclusive local date ranges (fixes the old date filter).
- Daily and monthly summaries; CSV (formula-safe) and PDF exports.
- Dashboard: clearly defined KPIs, students vs staff, per-classroom occupancy, alerts with their basis.

### People and accounts
- Staff support (`person_type`), roster CSV import with dry-run validation, enrollment quality gates,
  wrong-person warnings, face-record listing and erasure.
- Roles viewer / operator / admin enforced server-side; self-registration creates viewers; user management and
  audit log; `scripts/manage_users.py`.

### Frontend
- New design system and responsive sidebar shell; redesigned Dashboard and Live Monitoring; new Reports,
  Timetable and Users pages; presence sessions; student detail drawer; role-aware controls; toasts.

### Quality
- Backend tests 230 → 326; frontend tests 139 → 168; benchmark and accuracy-evaluation scripts; full
  documentation set in `docs/`.
- `backend/pytest.ini` prevents accidental collection of legacy scripts that call live servers.
