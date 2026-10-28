# Testing, performance and accuracy evaluation

## Automated tests

| Suite | Command (PowerShell) | Result on 20 Oct 2026 |
|---|---|---|
| Backend | `cd backend; .\.venv\Scripts\python.exe -m pytest` | **326 passed** |
| Backend legacy in-app tests | `.\.venv\Scripts\python.exe -m pytest app/services_test.py app/test_recognition_logic.py` | 9 passed |
| Frontend tests | `cd frontend; $env:CI="true"; npx react-scripts test --watchAll=false` | **168 passed** (10 suites) |
| Type check | `npx tsc --noEmit -p .` | 0 errors |
| Lint | `npx eslint src --ext .js` | 0 problems (`src/App.test.tsx`, a leftover file, has 1 pre-existing `prefer-screen-queries` error when TypeScript files are linted too) |
| Production build | `npm run build` | compiled successfully |

`backend\pytest.ini` restricts collection to `backend\tests`. The many `test_*.py` scripts in the `backend\`
folder itself are legacy scripts that call a live server or the developer database at import time; they are never
collected.

Every test uses temporary SQLite files or an in-memory database; none touches `data/attendai.db`, a camera or the
network. Camera tests replace `cv2.VideoCapture` with fakes.

### What the backend suite covers

| Area | Files |
|---|---|
| Attendance engine: ENTRY/EXIT rules, cooldowns, stability, concurrency, restart, midnight | `test_attendance_engine.py`, `test_day_boundaries.py`, `test_integrity_and_timestamps.py` |
| Face matching and KNOWN/UNKNOWN/AMBIGUOUS classification | `test_face_matching.py`, `test_recognition_integration.py` |
| Unregistered occupancy: boundaries, drift, merges, midnight, concurrency, pipeline integration, group entry, slow frames | `test_unknown_occupancy.py` (67), `test_recognition_integration.py` |
| Camera pipeline, reconnect/backoff, end of file, stale feeds, credential masking, source validation, missing model, shared device | `test_video_pipeline.py`, `test_camera_reliability.py` |
| Roles, registration, user admin, audit log | `test_permissions.py` |
| Period attendance, exceptions, coverage, overrides, holidays, overlaps, daily/monthly, sessions, manual events, CSV/PDF exports | `test_period_attendance.py` |
| Roster CSV import, enrollment quality gates, wrong-person warnings, face erasure | `test_roster_import.py`, `test_enrollment_quality.py`, `test_soft_delete_and_enrollment.py` |
| Dashboard figures, health, configuration, requirements pins | `test_dashboard_and_recent.py`, `test_health.py`, `test_auth_and_config.py`, `test_requirements.py` |
| Migrations on disposable databases (upgrade keeps rows, guarded downgrade, offline mode, model parity) | `test_migrations.py`, `test_migrations_e7.py` |
| **Mocked integration**: login → setup → enrollment → camera worker → ENTRY → dashboard/period/report agree → stop | `test_end_to_end_mocked.py` |
| Operator scripts (evaluation scoring, user CLI) | `test_scripts.py` |

"Mocked integration" means the computer-vision models are replaced by stubs that "see" chosen synthetic
encodings. It proves the plumbing and the consistency of the stored data, **not** recognition accuracy.

## Manual end-to-end check (20 Oct 2026)

Run on an isolated instance (backend on port 8001 with a fresh scratch database, frontend dev server on 3001;
the real database was not used and its SHA-256 was unchanged afterwards):

- Self-registration created a viewer; `scripts/manage_users.py set-role` promoted it; login through the UI.
- Roster CSV import (9 fictional people), classroom, IN/OUT cameras (placeholder files), three periods, four
  audited manual events.
- Dashboard: present/inside/unregistered figures, alerts with reasons, recent events.
- Live Monitoring: start with confirmation, synthetic frames clearly labelled, frames served as JPEG with
  `Cache-Control: no-store`, ~67 ms per frame, stop with confirmation, camera run recorded.
- Reports (period, daily), presence sessions, Students, Cameras, Timetable, Users and audit log rendered with
  correct data. Two defects found this way were fixed and covered by tests (an upcoming period showed people
  already inside as Present; a running period reported "left early").

Not covered: a real webcam or CCTV stream, real faces, PDF/CSV downloads saved through the browser (the export
endpoints are covered by API tests).

## Performance

Development machine: Windows 11, AMD Ryzen 7 7735U (8 cores / 16 threads), 16 GB RAM, **no CUDA GPU**.
Reproduce with `cd backend; .\.venv\Scripts\python.exe scripts\benchmark_pipeline.py --out ..\docs\benchmarks\<name>`
(`--video <file>` to use recorded footage).

Synthetic frames (no people, so this is the fixed per-frame cost), 640×480, 15 s per run
([benchmarks/benchmark-synthetic.md](benchmarks/benchmark-synthetic.md)):

| Cameras | Throughput | Mean / p95 latency per frame | Process CPU | RSS |
|---|---|---|---|---|
| 1 | 19.3 frames/s | 41 / 48 ms | 787 % (≈ 8 cores) | 449 MB |
| 2 | 15.2 frames/s per camera | 60 / 70 ms | 1437 % | 536 MB |

Face matching (linear scan, 3 encodings per person): 0.09 ms per face for 60 people, 1.7 ms for 500, 7.8 ms for
2000 — not a bottleneck.

Per person and per face (measured separately, CPU):

| Step | Cost |
|---|---|
| HOG face detection in a 200×400 person crop | 32 ms |
| dlib face encoding, `num_jitters=1` (default) | **565 ms** |
| dlib face encoding, `num_jitters=0` | 277 ms |

The installed dlib wheel reports `USE_AVX_INSTRUCTIONS=False`; encoding dominates. A frame with 5 visible faces
therefore takes roughly 3 s on this machine. The pipeline widens its consecutive-sighting gap automatically for
slow frames (tested), so group entries are still confirmed, just later. To speed up: build dlib with AVX (expected
several times faster, not measured), set `FACE_ENCODING_JITTERS=0` after measuring accuracy, or use a GPU.

**Not measured:** latency with real faces in real footage, 10 simultaneous cameras, 500 enrolled people with real
photos, or event-to-screen latency on the target hardware. Run the benchmark with `--video` on the deployment
machine before committing to a scale.

## Accuracy evaluation protocol

No accuracy figure is claimed: nothing has been measured with real people yet. `scripts/evaluate_accuracy.py`
implements the measurement.

1. **Consent and data.** Only consenting participants; enroll 2–4 photos each. Recruit 3–5 unregistered
   volunteers. Record the session (or note times live) only with permission.
2. **Ground truth.** A human writes one row per real door crossing in `ground_truth.csv`
   (`person,event_type,timestamp,notes`; visitors as `VISITOR-<n>`). Note conditions (lighting, caps, masks,
   group size, occlusion). Optionally log the dashboard counts at fixed times in `occupancy_log.csv`.
3. **Scenarios** (each a separate run): single file entry/exit; groups of 3–6; caps/glasses/partial occlusion;
   poor and back-lighting; quick re-entry; break then return; unregistered visitors entering and leaving.
4. **Evaluate** (database opened read-only):
   ```powershell
   .\.venv\Scripts\python.exe scripts\evaluate_accuracy.py --db data\attendai.db --ground-truth eval\ground_truth.csv `
       --start 2026-10-21T08:55 --end 2026-10-21T10:00 --occupancy-log eval\occupancy_log.csv --out eval\report
   ```
5. **Metrics.** Each crossing is one case (frames are never counted as cases):
   - *identification accuracy* = matched ÷ (enrolled crossings + wrong-identity events + spurious events)
   - *recall* (missed registered crossings), *precision*, *wrong identity* (false acceptance), *spurious*
     (including duplicates), timestamp error (mean/median/p95/max)
   - unregistered occupancy: mean absolute error and % of samples exactly right
6. **Report** numbers per scenario together with the conditions. The Phase I threshold (≥ 50 %) should be judged on
   identification accuracy over the full scenario set.
7. **Calibrate** `FACE_MATCH_TOLERANCE`, `FACE_MATCH_AMBIGUITY_MARGIN`, `ATTENDANCE_MIN_CONFIDENCE` and
   `UNKNOWN_*_DISTANCE` using a session that is *not* the one used for the final figures.
