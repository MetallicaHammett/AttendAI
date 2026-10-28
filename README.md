# AttendAI

On-premise, camera-based classroom attendance and occupancy system. An entry (IN) camera and an exit (OUT)
camera identify enrolled students and staff by face, record ENTRY and EXIT events with timestamps, estimate
how many **unregistered** people are inside, and compute attendance for scheduled class periods, with
exceptions, manual corrections, daily and monthly reports.

Everything runs on one Windows machine: FastAPI + SQLite backend, React frontend, local computer-vision
models. No cloud service is used during operation.

> **Status (1.0, 20 Oct 2026).** The software is complete and tested with automated and mocked integration
> tests, and was walked through end to end in a browser on an isolated scratch database. It has **not yet
> been validated with real CCTV cameras, real enrolled participants or labelled footage**, so recognition
> accuracy is unmeasured. See [docs/REQUIREMENTS_TRACEABILITY.md](docs/REQUIREMENTS_TRACEABILITY.md) and
> [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## Features

| Area | What exists |
|---|---|
| Recognition | YOLOv8n person detection → DeepSORT tracking (every frame) → HOG face detection → 128-d dlib encodings → explicit **KNOWN / UNKNOWN / AMBIGUOUS** classification |
| Attendance | Shared thread-safe engine: one ENTRY per real entry, EXIT only after an entry, stability and cooldown rules, restart-safe (restored from stored events) |
| Unregistered people | Shared in-memory tracker: counted once at the IN camera, removed when the same face is seen at the OUT camera; ambiguous faces never counted |
| Periods | Classrooms, weekly timetable, holidays; per-period PRESENT / LATE / ABSENT / UNVERIFIED (no camera running) with exceptions (late, left early, did not return after break) |
| Corrections | Audited manual ENTRY/EXIT events and per-period overrides with reasons |
| Reports | Presence sessions (entry/exit pairs, time inside), daily and monthly summaries, CSV and PDF export |
| People | Students and staff, roster CSV import with row-level validation, face enrollment with quality checks and wrong-person warnings, erasure of face data |
| Cameras | Webcam, video file and RTSP; credentials masked; connection test; auto-reconnect; stale-feed detection; honest live / recorded / synthetic labels |
| Security | Roles (viewer / operator / admin) enforced server-side, audit log, safe self-registration, offline model loading, no telemetry |
| UI | Responsive sidebar app: Dashboard, Live Monitoring, Attendance, Reports, Students, Cameras, Timetable, Users |

## Quick start (Windows, PowerShell)

Prerequisites, first-time setup and the database upgrade are in [docs/SETUP_WINDOWS.md](docs/SETUP_WINDOWS.md).
Day to day:

```powershell
# Backend (one worker process - camera state is shared in memory)
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Frontend (development server on http://localhost:3000)
cd frontend
npm start
```

Health check: `http://127.0.0.1:8000/health`. API documentation: `http://127.0.0.1:8000/docs`.

**Upgrading an existing database is required** for this version (new tables and columns). Back up first;
the exact commands are in [docs/SETUP_WINDOWS.md](docs/SETUP_WINDOWS.md#upgrading-the-database). Until it is
upgraded, `/health` reports `"schema": "outdated"` and most pages fail.

## Documentation

| Document | Contents |
|---|---|
| [docs/SETUP_WINDOWS.md](docs/SETUP_WINDOWS.md) | Installation, model files, database upgrade, first admin, start/stop |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every environment variable |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Enrollment, roster import, cameras, timetable, corrections, reports, troubleshooting |
| [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md) | Step-by-step live demonstration (Teams) and fallback |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture and data-model diagrams, processing pipeline, API overview |
| [docs/SECURITY_PRIVACY.md](docs/SECURITY_PRIVACY.md) | Roles, data handling and retention, network use, licences |
| [docs/TESTING_AND_EVALUATION.md](docs/TESTING_AND_EVALUATION.md) | Test suites, benchmark results, accuracy evaluation protocol |
| [docs/REQUIREMENTS_TRACEABILITY.md](docs/REQUIREMENTS_TRACEABILITY.md) | Requirement → implementation → verification → status |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | Known limitations and what needs hardware or validation |
| [docs/HANDOVER.md](docs/HANDOVER.md) | Source-code guide for maintainers |
| [CHANGELOG.md](CHANGELOG.md) | Release summary |

## Licensing note

Person detection uses Ultralytics YOLOv8, which is **AGPL-3.0**. That is fine for evaluation, but institutional or
commercial deployment needs either AGPL compliance for the whole application or an Ultralytics Enterprise licence,
or replacing the detector. Details: [docs/SECURITY_PRIVACY.md](docs/SECURITY_PRIVACY.md#third-party-licences).
