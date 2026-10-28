# Security, privacy and licences

## Access control

- Roles **viewer < operator < admin**, enforced server-side on every endpoint (`app/permissions.py`); the UI only
  hides controls. Tests: `backend/tests/test_permissions.py`.
- Self-registration always creates a **viewer**; a role in the request is ignored. It can be disabled
  (`ALLOW_SELF_REGISTRATION=False`).
- Existing accounts became **operator** in the 1.0 migration; admins are promoted explicitly
  (`scripts/manage_users.py`). An admin cannot demote or deactivate their own account.
- Deactivating an account takes effect on its next request (tokens are re-checked against the database).
- Passwords: bcrypt; 8–72 characters (longer ones are rejected rather than silently truncated).
- Tokens: HS256 JWT, 30 minutes. In production the server refuses to start without a strong `SECRET_KEY`.
- Audit log (`audit_log` table, Users page): user changes, person/camera/classroom/period/holiday changes,
  roster imports, face enrollment and erasure, camera start/stop/test, manual events, overrides, attendance
  edits/deletions. Never contains passwords, tokens, stream credentials or face data.

## Data handling

| Data | Stored | Retention / deletion |
|---|---|---|
| Face photos | **Never** (decoded in memory, discarded) | – |
| Face encodings (128 numbers per photo) | `face_embeddings` | Until erased on the person's Details panel (permanent). Deleting a person deactivates them. |
| Unregistered visitors' encodings | Memory only | Discarded on exit, at local midnight, on recognition as a student, or on restart. Never logged or written to disk. |
| Attendance events | `attendance_events` | Kept indefinitely; admins can delete individual events (audited). Define a retention period institutionally. |
| Camera frames | Latest annotated frame in memory per running camera | Overwritten every frame; nothing is recorded. |
| Camera credentials | `cameras.source` in the database | Never returned by the API (`rtsp://***@host`), never logged, masked in errors and audit entries. Protect the database file. |
| Logs | Console | Student database IDs and camera IDs at INFO; no encodings, passwords or images. |

The database file and its backups contain personal data and password hashes: they are git-ignored; keep them on
an access-controlled disk and include them in the institution's backup policy.

## Input validation

- Images: JPEG/PNG only, ≤ 5 MB, ≤ 40 Mpixel, exactly one face, quality gates.
- Roster CSV: ≤ 1 MB, ≤ 5000 rows, strict columns, all-or-nothing import.
- Camera sources: webcams are device numbers; streams must be rtsp/http(s) URLs; video files must resolve
  inside `VIDEO_FILES_DIR` (path traversal and absolute paths outside it are rejected).
- CSV exports neutralise spreadsheet formulas (`=`, `+`, `-`, `@`).
- All request bodies are validated by Pydantic schemas (422 on malformed input).

## Network and cloud dependencies

| Check | Result |
|---|---|
| Model downloads at runtime | None: a missing `yolov8n.pt` or SFace/YuNet file is a setup error (`ModelUnavailableError`). dlib models are installed with the Python package. |
| Ultralytics internet check and analytics | Disabled in-process with `YOLO_OFFLINE=1` (set in `app/services.py` before import). Verified: `ONLINE=False` → events disabled. The user's global Ultralytics settings file is not modified. |
| Frontend external resources | None: system fonts, no CDN scripts, no analytics (`reportWebVitals` is not enabled). |
| Data leaving the machine | None by the application. Cameras are read over the local network. |

CORS is limited to `CORS_ORIGINS`. Responses carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on personal-data endpoints and frames.

## Known residual risks

- **No liveness / anti-spoofing.** A printed photo or a phone screen showing an enrolled face can be recognised.
  Nothing in the system detects this; do not use it where impersonation matters without a human check.
- **Token in browser `localStorage`.** An XSS bug would expose the 30-minute token. React escapes output and no
  third-party scripts are loaded, but httpOnly-cookie sessions would be stronger (requires CSRF protection).
- **No TLS in the app.** Use only on localhost or behind an HTTPS reverse proxy on the campus network.
- **SQLite file permissions** are the operating system's; anyone who can read the file can read the data.
- **Face recognition errors** can attribute an entry to the wrong student; corrections and overrides exist for this.

## Third-party licences

| Component | Licence (from installed package metadata) | Note |
|---|---|---|
| Ultralytics (YOLOv8 code and `yolov8n.pt` weights) | **AGPL-3.0** | Network use of an AGPL program obliges offering the complete corresponding source of the application to its users, or buying an Ultralytics Enterprise licence. **Resolve before any deployment beyond evaluation** — options: comply with AGPL, buy a licence, or replace the person detector (it is isolated in `PersonDetectionService`). |
| face_recognition, face_recognition_models | MIT | The bundled model files have their own provenance; the 68-point landmark model is trained on the iBUG 300-W dataset, whose terms restrict commercial use. AttendAI uses the default 5-point model for encodings, but the package ships both — verify with the dlib model licences before commercial use. |
| dlib | Boost Software License | |
| deep-sort-realtime | MIT | Used without its built-in embedders. |
| OpenCV | Apache-2.0 | |
| OpenCV Zoo YuNet + SFace models (`backend\models\face`) | Apache-2.0 | The default face detector and recogniser. Free for any use including production. |
| PyTorch | BSD-style / Apache-2.0 | |
| FastAPI, SQLAlchemy, python-jose | MIT | |
| matplotlib | PSF-based | PDF reports. |
| React, MUI, recharts, axios | MIT | |

No commercial attendance product is used; all attendance logic is this project's own code.
