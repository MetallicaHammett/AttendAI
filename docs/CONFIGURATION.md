# Configuration reference

Settings are read from the environment and from `backend\.env` (`app/config.py`). Names are case-sensitive.
Never commit `.env`; `..\.env.example` is the secret-free template.

## Core

| Variable | Default | Meaning |
|---|---|---|
| `ENVIRONMENT` | *(required)* | `development`, `test` or `production`. Production refuses to start with a weak `SECRET_KEY`, `DEBUG=True` or synthetic cameras. |
| `SECRET_KEY` | *(required in production)* | Signs login tokens. ≥ 32 characters. Without it (dev/test) a random per-process key is used and logins reset on restart. |
| `DATABASE_URL` | `sqlite:///./data/attendai.db` | Relative to `backend\`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Session length. |
| `DEBUG` | `False` | FastAPI debug mode. Keep off outside development. |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001` | Browser origins allowed to call the API (localhost and 127.0.0.1 are different origins). |
| `ALLOW_SELF_REGISTRATION` | `False` | `/register` page. Off unless explicitly set to `True`; new accounts are always read-only viewers. |

## Cameras and models

| Variable | Default | Meaning |
|---|---|---|
| `YOLO_MODEL_PATH` | `yolov8n.pt` | Person-detection model, relative to `backend\`. Missing = setup error, never a download. |
| `VIDEO_FILES_DIR` | `data/videos` | Video-file cameras must point inside this folder (stops the API from opening arbitrary files). |
| `ENABLE_SYNTHETIC_CAMERA` | `False` | Development only: an unopenable source produces clearly labelled synthetic frames instead of an error. |
| `CAMERA_AUTO_RECONNECT` | `True` | Reopen a dropped webcam/RTSP source with exponential backoff (2, 4, 8 … s). |
| `CAMERA_RECONNECT_MAX_DELAY_SECONDS` | `30` | Backoff ceiling. |
| `CAMERA_STALE_AFTER_SECONDS` | `5` | No new frame for this long → feed reported as stale. |
| `CAMERA_LATEST_FRAME_ONLY` | `True` | Live feeds (webcam/RTSP) are read continuously on their own thread and only the newest frame is processed, so slow recognition never falls behind real time. Skipped frames are reported as `frames_skipped` in the camera status. Recorded files always process every frame. |
| `CAMERA_OPEN_TIMEOUT_SECONDS` | `10` | Network streams: give up opening after this long (the camera **Test** uses the same limit). |
| `CAMERA_READ_TIMEOUT_SECONDS` | `5` | Network streams: a read that stalls this long counts as a failed read; repeated failures → disconnected → reconnect. |
| `OPENCV_FFMPEG_CAPTURE_OPTIONS` | `rtsp_transport;tcp` | Set by the backend unless already in the environment: RTSP over TCP (UDP loses packets on busy networks and smears frames). Set `rtsp_transport;udp` only if a camera refuses TCP. |
| `FACE_DETECTION_UPSAMPLE` | `1` | dlib upsampling before HOG face detection. `1` finds faces from ~40 px; `2` finds ~20 px faces (distant CCTV) at roughly 4× the detection cost. Measure on the real feed before changing. |
| `FACE_RELOAD_INTERVAL_SECONDS` | `30` | Running cameras also reload enrolled faces at once after any enrollment change. |

## Recognition model and thresholds

These values are starting points and **must be calibrated with labelled footage from the real cameras**
([TESTING_AND_EVALUATION.md](TESTING_AND_EVALUATION.md)).

| Variable | Default | Meaning |
|---|---|---|
| `FACE_RECOGNITION_MODEL` | `sface` | `sface` = OpenCV YuNet + SFace (files in `backend\models\face`, see SETUP_WINDOWS.md §3); `dlib` = the original face_recognition model. Each model only uses faces enrolled with it. |
| `FACE_SFACE_MATCH_DISTANCE` | `0.637` | SFace: candidate match within this cosine distance (1 − cosine similarity); OpenCV's published same-person threshold, cosine 0.363. |
| `FACE_SFACE_MIN_CONFIDENCE` | `60` | SFace: confidence = cosine similarity × 100. Recording attendance needs at least this in every one of the consecutive frames. Calibrated on the enrolled class photos (112 photos, 29 people; single-photo probes): unregistered people wrongly recorded 3.6% (10.7% at 50), wrong student 0.9%, enrolled student recorded 70% per frame. Confirm on real CCTV footage. |
| `FACE_SFACE_LOOKALIKE_SIMILARITY` | `0.55` | SFace enrollment safeguard: a new photo at least this similar (cosine) to *another* enrolled student is refused. Separate from the match limit because ~15% of different-person pairs in the class photos exceed 0.363 but only 0.3% exceed 0.55. The "unlike this student's own photos" check still uses the match limit. |
| `FACE_SFACE_DETECTION_SCORE` | `0.8` | YuNet score needed to treat a region as a face. |
| `FACE_MIN_SIZE_PIXELS` | `50` | Camera faces smaller than this (shorter side) are skipped, never matched or counted as unregistered; counted as `total_low_quality_faces` in the camera status. `0` = off. |
| `FACE_MIN_SHARPNESS` | `15` | Same, for blur (variance of the Laplacian over the face). `0` = off. |
| `FACE_MATCH_AMBIGUITY_MARGIN` | `0.06` | The best match must lead the next-closest *different* student by more than this distance, otherwise the face is AMBIGUOUS (not counted as anyone, not counted as unregistered). Applies to both models. |
| `FACE_ENROLLMENT_JITTERS` | `10` | dlib only: re-sampling per enrollment photo, for a steadier reference encoding. |

dlib (`FACE_RECOGNITION_MODEL=dlib`): distances are Euclidean (L2) between 128-d dlib encodings. Lower = more similar.

| Variable | Default | Meaning |
|---|---|---|
| `FACE_MATCH_TOLERANCE` | `0.6` | A face is a candidate match for a student only within this distance (dlib's reference value). |
| `ATTENDANCE_MIN_CONFIDENCE` | `70` | Match score = (1 − distance) × 100. 70 means distance ≤ 0.30, stricter than the tolerance. Not a probability. |
| `FACE_ENCODING_JITTERS` | `1` | dlib re-sampling per encoding at the cameras. `0` roughly halves encoding time on CPU (measured 277 vs 565 ms per face on the development machine); measure accuracy before changing. |
| `UNKNOWN_SAME_PERSON_DISTANCE` | `0.6` | Two unregistered faces this close are treated as the same visitor (IN ↔ OUT matching). |
| `UNKNOWN_RESOLVE_KNOWN_DISTANCE` | `0.45` | A confidently recognised student removes at most the nearest unregistered occupant within this distance (when they had first been counted as unknown). Must be ≤ the previous value. Provisional. |

## Attendance rules

| Variable | Default | Meaning |
|---|---|---|
| `ATTENDANCE_MIN_CONSECUTIVE_FRAMES` | `5` | Sightings needed (gaps ≤ max gap) before an ENTRY/EXIT or an unregistered entry is recorded. |
| `ATTENDANCE_MAX_FRAME_GAP_SECONDS` | `2.0` | A longer gap restarts the count. Each camera widens it automatically to 2.5× its measured frame interval (max 15 s) when frames are slow, so many faces per frame cannot prevent confirmation. |
| `ATTENDANCE_ENTRY_COOLDOWN_SECONDS` | `300` | Minimum time between two ENTRY events of a student. |
| `ATTENDANCE_EXIT_COOLDOWN_SECONDS` | `60` | Minimum time after an EXIT before a new ENTRY. |
| `ATTENDANCE_MIN_PRESENCE_SECONDS` | `60` | Minimum time inside before an EXIT is accepted. |
| `ATTENDANCE_LATE_AFTER` | *(unset)* | `HH:MM`; only for the dashboard's 7-day chart. Period lateness uses the timetable. |
| `ATTENDANCE_DEFAULT_GRACE_MINUTES` | `10` | Arriving after start + grace = LATE (per-period override in the timetable). |
| `ATTENDANCE_EARLY_ARRIVAL_MINUTES` | `15` | An ENTRY this long before the start counts for the period. |
| `ATTENDANCE_EARLY_DEPARTURE_MINUTES` | `10` | Leaving earlier than end − this, without returning = EARLY_DEPARTURE. |

Trade-offs: higher frame counts and cooldowns suppress flicker at the door but delay events and can miss people
who pass quickly; lower ones react faster but risk duplicate or spurious events. Change one value at a time and
re-run the evaluation.

## Frontend

| Variable | Where | Meaning |
|---|---|---|
| `REACT_APP_API_URL` | `frontend\.env` | Backend URL (default `http://localhost:8000`). Read at build/start time. |
