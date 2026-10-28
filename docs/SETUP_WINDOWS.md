# Setup on Windows

All commands are PowerShell, run from the repository folder `attendai\`.

## 1. Prerequisites

| Component | Version used and tested |
|---|---|
| Windows | 11 Pro |
| Python | 3.12 in `backend\.venv` (the separate `backend\venv` with Python 3.14 has dependency problems - do not use it) |
| Node.js | 18 or later (tested with v24) |
| Disk | ~3 GB for Python packages (PyTorch CPU), ~600 MB for `node_modules` |
| Camera | Webcam, video files, or RTSP/IP cameras reachable from this machine |

`dlib` needs a C++ build toolchain where no prebuilt wheel exists (Visual Studio Build Tools + CMake).

## 2. Backend environment

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Skip this if `backend\.venv` already exists and works (`.\.venv\Scripts\python.exe -m pytest` passes).

## 3. Model files (offline)

AttendAI never downloads models at runtime.

| File | Where | Used for |
|---|---|---|
| `yolov8n.pt` | `backend\yolov8n.pt` (or set `YOLO_MODEL_PATH`) | person detection |
| `face_detection_yunet_2023mar.onnx` (232,589 bytes) | `backend\models\face\` | face detection (default model, SFace) |
| `face_recognition_sface_2021dec.onnx` (38,696,353 bytes) | `backend\models\face\` | face recognition (default model, SFace) |
| dlib face models | installed by the `face_recognition_models` package | only when `FACE_RECOGNITION_MODEL=dlib` |

The two ONNX files come from the official OpenCV model zoo (`github.com/opencv/opencv_zoo`, folders
`models/face_detection_yunet` and `models/face_recognition_sface`, Apache-2.0). Check them before use:
SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` (YuNet) and
`0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` (SFace). If either is missing, starting a
camera and enrolling a face fail with a message naming the file.

**Switching face model** (`FACE_RECOGNITION_MODEL`, default `sface`): each model only uses faces enrolled
with it, so after switching, the Students page shows people enrolled with the other model as "Not enrolled"
until a new photo is added. Their older face records are kept and work again if you switch back.

If `yolov8n.pt` is missing, `/health` reports `"person_detection_model": "missing"`, the top bar shows
"API online · model missing", and starting a camera fails with a message naming the expected path. Obtain the
file once on a connected machine (it is the standard Ultralytics YOLOv8n checkpoint) and copy it into
`backend\`. Model files are git-ignored.

The backend sets `YOLO_OFFLINE=1` before Ultralytics is imported, which disables its internet check and usage
analytics without changing your global Ultralytics settings.

## 4. Configuration

Create `backend\.env` (it is git-ignored) from the template in `..\.env.example`. At minimum:

```ini
ENVIRONMENT=development
SECRET_KEY=<64 random characters>
DATABASE_URL=sqlite:///./data/attendai.db
```

Generate a key with:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

In `production` a missing/short key, `DEBUG=True` or `ENABLE_SYNTHETIC_CAMERA=True` stops the server at startup.
All settings: [CONFIGURATION.md](CONFIGURATION.md).

## 5. Database

### New installation

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
```

### Upgrading the database

Version 1.0 adds migration `e7b3c1a9d2f0` (roles, staff, classrooms, timetable, holidays, overrides, audit log,
camera runs). It is additive: no row or column is removed. Existing accounts become **operator** (nobody becomes
admin automatically), existing people become `student`, existing events become `camera` events.

It was verified on a copy of the current database: all 56 users, 6 students, 4 face records and 4 cameras were
preserved, `PRAGMA integrity_check` returned `ok` and there were no foreign-key violations.

```powershell
cd backend
# 1. Stop the backend. 2. Back up the database (keep this file; it is git-ignored)
Copy-Item data\attendai.db ("data\attendai.db.bak-" + (Get-Date -Format yyyyMMdd-HHmmss) + "-pre-e7b3c1a9d2f0")
# 3. Upgrade (alembic.ini points at data\attendai.db)
.\.venv\Scripts\python.exe -m alembic upgrade head
# 4. Check
.\.venv\Scripts\python.exe -m alembic current
```

Roll back (only if needed; refuses to drop data created by 1.0 unless forced):

```powershell
.\.venv\Scripts\python.exe -m alembic downgrade d5a1e7c2b9f4
```

## 6. First administrator

Promote an existing account (or create one) from the server console:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\manage_users.py list
.\.venv\Scripts\python.exe scripts\manage_users.py set-role <username> admin
.\.venv\Scripts\python.exe scripts\manage_users.py create <username> <email> --role admin   # prompts for the password
```

After that, an administrator creates further accounts on the **Users** page (**Create account**): choose the role, set an initial password and hand it over in person. Usernames and emails must be unique ignoring letter case, and neither the console nor the API will demote or deactivate the last active administrator.

**Student accounts.** Choose the role **Student** and pick the student's record. The student then signs in to a single page, **Attendance Details** (course-wise percentages, attendance hours and the course table); every staff page and API is refused. One account per student; a student account can be deactivated but never given a staff role. Staff can open the same page for any student from **Students → Attendance**. From the console:

```powershell
.\.venv\Scripts\python.exe scripts\manage_users.py create <username> <email> --student <student ID>   # prompts for the password
```

**Professor accounts** (the staff portal; see OPERATIONS.md, "Professor portal") can also be created from the console, then assigned to their periods on the **Timetable** page:

```powershell
.\.venv\Scripts\python.exe scripts\manage_users.py create <username> <email> --role professor   # prompts for the password
```

The attendance figures count each completed class period as one hour. Late arrivals count as attended, OD/ML is a period excused by staff with a reason, and periods with no entry camera running are left out rather than counted as absent.

Forgotten password (console only; there is deliberately no web reset):

```powershell
.\.venv\Scripts\python.exe scripts\manage_users.py set-password <username>   # hidden prompt, typed twice
```

Every change is written to the audit log (user "console"; never the password or its hash). Review the roles of the existing accounts on the
Users page afterwards; deactivate the ones nobody uses.

## 7. Start and stop

```powershell
# Backend: exactly ONE worker process (camera workers and occupancy state live in memory)
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Frontend (development)
cd frontend
npm install        # first time only
npm start          # http://localhost:3000
```

Production-style frontend: `npm run build` and serve `frontend\build` from any static web server on the same
machine; set `REACT_APP_API_URL` before building and add the frontend origin to `CORS_ORIGINS`.

Stop with Ctrl+C. On shutdown the backend stops all camera workers and closes their coverage records. Camera
processing keeps running while the browser is closed, until it is stopped on the Live Monitoring page or the
backend stops.

Use `--host 0.0.0.0` only on a trusted network, behind the institution's firewall; the API has no TLS of its own
(put a reverse proxy with HTTPS in front for anything beyond a single machine).
