# Source-code handover guide

## Layout

```
attendai/
  backend/
    app/
      main.py              FastAPI app, CORS, security headers, startup/shutdown, /health
      config.py            all settings (pydantic-settings), production safety checks
      database.py          engine, sessions, SQLite FK pragma, schema-version check
      models.py            SQLAlchemy models (see docs/ARCHITECTURE.md ER diagram)
      schemas.py           request/response models (UTC timestamps, masking, validation)
      permissions.py       roles (viewer/operator/admin) + audit() helper
      services.py          computer-vision pipeline, AttendanceEngine, UnknownOccupancyTracker
      attendance_logic.py  period attendance, exceptions, sessions, daily/monthly summaries
      roster_import.py     CSV parsing and validation (no DB writes)
      sources.py           camera source validation and credential masking
      crud.py              queries, dashboard statistics, local-day time helpers
      routers/             one module per URL prefix (auth, users, students, face_enrollment,
                           cameras, video, attendance, timetable, dashboard, reports, enrollment)
    alembic/versions/      migrations (latest e7b3c1a9d2f0)
    scripts/               manage_users.py, benchmark_pipeline.py, evaluate_accuracy.py
    tests/                 pytest suite (pytest.ini limits collection to this folder)
  frontend/src/
    App.js                 routes; theme.js design tokens; data.js API client
    components/            Layout (shell), ui.js (shared parts), dialogs, SessionsView, drawers
    context/               AuthContext (session, roles), NotifyContext (toasts)
    pages/                 one page per route + tests
  docs/                    this documentation
```

## Key design decisions

1. **One process, shared in-memory state.** One `AttendanceEngine` and one `UnknownOccupancyTracker` are shared by
   all camera threads (`services.get_shared_*`). This is what makes IN/OUT consistent and prevents duplicate events
   from two cameras. Do not run several Uvicorn workers.
2. **The database is the record; memory is the cache.** ENTRY/EXIT are persisted inside the engine lock; a failed
   write rolls the engine state back. On camera start the engine is re-seeded from the last 24 h of events.
3. **Classification before decisions.** `FaceDetectionService.classify_encoding` returns KNOWN / UNKNOWN /
   AMBIGUOUS. Only KNOWN reaches attendance, only UNKNOWN reaches occupancy; AMBIGUOUS is logged only.
4. **Honesty over completeness.** "Unverified" when no camera ran, "no exit recorded" for unpaired events,
   feed kinds live/recorded/synthetic, estimates labelled as estimates.
5. **Privacy by default.** Photos are never stored; visitor encodings never leave memory; credentials never leave
   the server; audit details never contain secrets or face data.

## Common changes

| Task | Where |
|---|---|
| New setting | `app/config.py` + `docs/CONFIGURATION.md` |
| New column/table | `app/models.py` + a new Alembic revision (additive, guarded downgrade; see `e7b3c1a9d2f0`) + a migration test |
| New endpoint | router in `app/routers/`, a `require_role(...)` dependency, `audit(...)` for privileged writes, tests with the `api` fixture |
| Change attendance rules | `services.AttendanceEngine` (live decisions) or `attendance_logic.py` (period status); both have dedicated tests |
| Replace the person detector (e.g. for licensing) | `services.PersonDetectionService`: keep `detect_persons()` returning `DetectionResult` and `check_model_file()` |
| Replace face recognition | `FaceDetectionService.detect_faces` / `classify_encoding`; re-tune thresholds; re-enroll everyone (encodings are model-specific) |
| New page | `frontend/src/pages`, route in `App.js`, entry in `NAV_SECTIONS` (`Layout.js`), use `components/ui.js` and the shared `api` client |

## Running the checks

```powershell
cd backend;  .\.venv\Scripts\python.exe -m pytest
cd ..\frontend;  $env:CI="true"; npx react-scripts test --watchAll=false; npx tsc --noEmit -p .; npx eslint src --ext .js; npm run build
```

Test fixtures: `backend/tests/conftest.py` (`api` = the real app on a temporary database with an admin token),
`backend/tests/helpers.py`; frontend `src/testUtils/mockApi.js` replaces the axios adapter, so interceptors (auth
header, 401 sign-out) run for real.
