# Architecture

A modular monolith on one machine: one FastAPI process (one worker) runs the API and one background thread per
running camera; SQLite stores records; the React app is static files talking to the API over HTTP.

## System

```mermaid
flowchart LR
  subgraph Cameras
    IN[IN camera<br/>webcam / RTSP / file]
    OUT[OUT camera]
  end
  subgraph Backend["FastAPI process (1 worker)"]
    W1[Camera worker thread<br/>VideoProcessingService]
    W2[Camera worker thread]
    ENG[(AttendanceEngine<br/>shared, in memory)]
    UNK[(UnknownOccupancyTracker<br/>shared, in memory)]
    API[REST API + roles + audit]
    LOGIC[attendance_logic<br/>periods, sessions, summaries]
    REP[reports CSV/PDF]
  end
  DB[(SQLite<br/>data/attendai.db)]
  UI[React app<br/>browser]
  IN --> W1
  OUT --> W2
  W1 --> ENG
  W2 --> ENG
  W1 --> UNK
  W2 --> UNK
  ENG -- ENTRY/EXIT --> DB
  W1 -- camera_runs --> DB
  API --> DB
  API --> LOGIC --> DB
  API --> REP --> LOGIC
  API --> ENG
  API --> UNK
  UI -- JWT --> API
```

## Per-frame pipeline (`app/services.py`)

```mermaid
flowchart TD
  F[Read frame<br/>reconnect with backoff, end-of-file, stale detection] --> P[YOLOv8n person detection]
  P --> T[DeepSORT tracking<br/>every frame, also with no faces]
  P --> D[HOG face detection inside each person box]
  D --> E[128-d dlib encodings]
  E --> C{classify_encoding}
  C -- KNOWN --> A[AttendanceEngine.process_recognition<br/>stability, cooldowns, IN/OUT rules,<br/>persist under lock, rollback on failure]
  A -- confident --> R[UnknownOccupancyTracker.resolve_known<br/>remove nearest unknown within 0.45]
  C -- UNKNOWN --> U[UnknownOccupancyTracker.observe<br/>count at IN, remove at OUT]
  C -- AMBIGUOUS --> X[logged, never counted]
  A --> L[recent observations log]
  U --> L
  L --> V[annotated JPEG for /video/id/frame]
```

Classification (`FaceDetectionService.classify_encoding`): for each enrolled person take the closest encoding;
best distance > tolerance → UNKNOWN; a different person also within tolerance and within the ambiguity margin →
AMBIGUOUS; otherwise KNOWN with score (1 − distance) × 100. Nobody enrolled → UNKNOWN.

Attendance rules (`AttendanceEngine`): per student a state machine OUTSIDE → ENTRY (IN camera) → INSIDE → EXIT (OUT
camera, after minimum presence). Recognitions must repeat on consecutive frames; gaps reset the count. One engine
and one tracker are shared by all cameras, so IN and OUT see the same state and two cameras cannot both record
the same entry. On start the engine is re-seeded from the last 24 h of stored events (restart safety).

## Data model

```mermaid
erDiagram
  USERS ||--o{ AUDIT_LOG : writes
  USERS ||--o{ ATTENDANCE_EVENTS : "creates (manual)"
  USERS ||--o{ ATTENDANCE_OVERRIDES : creates
  STUDENTS ||--o{ FACE_EMBEDDINGS : has
  STUDENTS ||--o{ ATTENDANCE_EVENTS : has
  STUDENTS ||--o{ ENROLLMENTS : has
  STUDENTS ||--o{ ATTENDANCE_OVERRIDES : has
  CAMERAS ||--o{ ATTENDANCE_EVENTS : records
  CAMERAS ||--o{ CAMERA_RUNS : "ran during"
  CLASSROOMS ||--o{ CAMERAS : contains
  CLASSROOMS ||--o{ TIMETABLE_PERIODS : hosts
  TIMETABLE_PERIODS ||--o{ ATTENDANCE_OVERRIDES : has

  USERS { int id string username string email string role "viewer|operator|admin" bool is_active }
  STUDENTS { int id string student_id string name string class_name string person_type "student|staff" bool is_active datetime deleted_at }
  FACE_EMBEDDINGS { int id int student_id blob embedding_data "128 float64" bool is_active }
  CAMERAS { int id string name string source string source_type string camera_type "IN|OUT" int classroom_id datetime deleted_at }
  ATTENDANCE_EVENTS { int id int student_id int camera_id string event_type "ENTRY|EXIT" datetime timestamp "naive UTC" int confidence string source "camera|manual" text note }
  CLASSROOMS { int id string name int capacity datetime deleted_at }
  TIMETABLE_PERIODS { int id int classroom_id string class_name string course_name int day_of_week time start_time time end_time int grace_minutes bool is_active }
  HOLIDAYS { int id date date string name }
  ATTENDANCE_OVERRIDES { int id int period_id date date int student_id string status text reason }
  AUDIT_LOG { int id datetime created_at string username string action string entity_type text details }
  CAMERA_RUNS { int id int camera_id datetime started_at datetime stopped_at datetime last_heartbeat_at string feed_kind }
  ENROLLMENTS { int id int student_id string course_code string semester }
```

- Students, cameras and classrooms are soft-deleted so history stays intact; periods are deactivated.
- `attendance_events.timestamp` is written by SQLite (`CURRENT_TIMESTAMP`, naive UTC); day boundaries are the
  server's local days (`crud.local_day_start_utc`, `timestamp_at_or_after`).
- `camera_runs` records when cameras actually ran, so a missing entry during an uncovered period is reported
  as *Unverified*, not *Absent*.
- Not persisted on purpose: unregistered-person encodings and counts (memory only), photos (never stored).

## API overview

Interactive documentation with every schema: `http://127.0.0.1:8000/docs`. All endpoints except `/health`,
`/auth/login` and `/auth/register` need `Authorization: Bearer <token>`.

| Prefix | Purpose | Minimum role for writes |
|---|---|---|
| `/auth` | login (form), register (viewer), `me` | – |
| `/users` | accounts, roles, audit log | admin (also for reads) |
| `/students` | people CRUD, filters, `/classes`, `/import` (CSV, dry run), `/import/template` | operator (delete: admin) |
| `/face-enrollment` | enroll photo, list/erase face records | operator |
| `/cameras` | CRUD, toggle, `/{id}/test` | admin (test: operator) |
| `/video` | `/{id}/start`, `/{id}/stop`, `/{id}/status`, `/{id}/frame`, `/{id}/observations`, `/sessions`, `/live` | operator |
| `/attendance` | events (inclusive local date range), `/recent`, `/sessions`, `/manual` | operator (`POST /`, PUT, DELETE: admin) |
| `/timetable` | classrooms, periods, holidays, `/periods/{id}/attendance`, overrides, `/daily`, `/monthly` | admin (overrides: operator) |
| `/dashboard` | `/`, `/stats`, `/daily/{n}`, `/trend`, `/alerts` | – (read only) |
| `/reports` | `sessions.csv/.pdf`, `period.csv`, `daily.csv/.pdf`, `monthly.csv/.pdf` | – (read only) |
| `/enrollment` | course enrollments | operator |

## Frontend

`frontend/src`: `App.js` (routes), `components/Layout.js` (sidebar shell, health chip), `theme.js` (design
tokens), `components/ui.js` (shared page parts), `data.js` (the single axios client: token, 401 handling,
downloads), `context/AuthContext.js` (session, roles), one file per page in `pages/`. Polling intervals are
cleared on unmount; frames are fetched one at a time as blobs and their object URLs released.

## Scaling notes

Measured on the development laptop (CPU only, no GPU): see
[TESTING_AND_EVALUATION.md](TESTING_AND_EVALUATION.md#performance). Face matching is a linear scan
(~1.7 ms per face for 500 people × 3 photos), which is not the bottleneck; detection is. For ~500 students in
5 classrooms (10 cameras) on one machine expect to need a GPU or a lower processing rate per camera, measured on
the target hardware. SQLite is adequate for one process with one writer at a time; move to PostgreSQL only if
several backend processes are required (the in-memory engine/tracker would then need to move to a shared store).
