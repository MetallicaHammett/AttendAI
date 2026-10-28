import logging
import secrets
from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# Known placeholder values that must never be used to sign tokens
INSECURE_SECRET_KEYS = {
    "",
    "your-secret-key-here",
    "your-secret-key-here-change-in-production",
    "changeme",
    "secret",
}
MIN_SECRET_KEY_LENGTH = 32
ALLOWED_ENVIRONMENTS = ("development", "test", "production")
SECRET_KEY_HELP = (
    "Set SECRET_KEY in the environment or backend/.env to a random value of at least "
    f"{MIN_SECRET_KEY_LENGTH} characters, e.g. the output of: "
    "python -c \"import secrets; print(secrets.token_urlsafe(64))\""
)


class Settings(BaseSettings):
    # Server settings
    # Used only by `python -m app.main`; the documented uvicorn command passes --host itself.
    # Loopback by default: a missing setting must never expose the API to the network.
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    # Required, no default: "development", "test" or "production". Startup fails if
    # it is missing, so a deployment can never silently run with development rules.
    ENVIRONMENT: str = ""
    DEBUG: bool = False  # Must stay False in production

    # Security
    # Must come from the environment / .env. In production a missing, placeholder or
    # short key is a startup error. In development/test a random per-process key is
    # used with a warning (logins then reset on restart and differ between workers).
    # SecretStr keeps the value out of repr()/str()/logs; read it with
    # settings.SECRET_KEY.get_secret_value().
    SECRET_KEY: SecretStr = SecretStr("")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Database
    DATABASE_URL: str = "sqlite:///./data/attendai.db"

    # Video processing
    VIDEO_SOURCE: str = "0"  # 0 for default webcam, or path to video file
    FRAME_WIDTH: int = 640
    FRAME_HEIGHT: int = 480
    FPS: int = 30
    # Development/test only: when True, a camera that cannot be opened is replaced
    # by synthetic frames instead of failing. Never enable for real attendance.
    ENABLE_SYNTHETIC_CAMERA: bool = False

    # Face recognition model (app/face_models.py). "sface": OpenCV YuNet detector + SFace
    # recognizer (opencv_zoo, Apache-2.0; ONNX files in backend/models/face), far more robust
    # than dlib on angled, small and low-light faces. "dlib": the original face_recognition
    # model. Each model only uses faces enrolled with it, so after switching, people need a
    # photo enrolled with the new model (Students page shows who is "Not enrolled").
    FACE_RECOGNITION_MODEL: str = "sface"
    FACE_SFACE_DETECTOR_PATH: str = "models/face/face_detection_yunet_2023mar.onnx"
    FACE_SFACE_RECOGNIZER_PATH: str = "models/face/face_recognition_sface_2021dec.onnx"
    FACE_SFACE_DETECTION_SCORE: float = 0.8  # YuNet face score needed to treat a box as a face
    # SFace compares by cosine distance (1 - cosine similarity). 0.637 = OpenCV's published
    # same-person cosine threshold 0.363: a candidate match only.
    FACE_SFACE_MATCH_DISTANCE: float = 0.637
    # Confidence for SFace = cosine similarity x 100. Recording attendance needs >= this in
    # every one of ATTENDANCE_MIN_CONSECUTIVE_FRAMES frames. 60 calibrated on the enrolled
    # Cybersecurity class photos (2026-10-22, 112 photos / 29 people, single-photo probes):
    # unregistered people wrongly recorded 3.6% (10.7% at 50), wrong student 0.9%, enrolled
    # student recorded 70%. Confirm on real CCTV footage (scripts/evaluate_accuracy.py).
    FACE_SFACE_MIN_CONFIDENCE: float = 60.0
    # Enrollment safeguard (SFace): a new photo this similar (cosine) to ANOTHER enrolled student
    # is refused as a possible wrong-record selection. Separate from the match limit because, on
    # the class photos, ~15% of different-person pairs exceed 0.363 while only 0.3% exceed 0.55.
    FACE_SFACE_LOOKALIKE_SIMILARITY: float = 0.55

    # dlib face matching (FACE_RECOGNITION_MODEL="dlib").
    # Metric: Euclidean (L2) distance between 128-d dlib face encodings, as used by
    # face_recognition.face_distance. Lower = more similar; 0.6 is dlib's reference
    # tolerance. A face is a candidate match for a student only if its best distance
    # to that student's enrolled encodings is <= FACE_MATCH_TOLERANCE.
    FACE_MATCH_TOLERANCE: float = 0.6
    # If the runner-up *different* student is within this distance of the best
    # student, the match is treated as ambiguous (never attributed to either). The best
    # match must lead clearly: 0.06 refuses look-alikes; 0.0 = exact ties only.
    FACE_MATCH_AMBIGUITY_MARGIN: float = 0.06
    # Camera faces smaller than this (shorter side, px) or blurrier than this (variance of
    # the Laplacian over the face) are skipped instead of matched: tiny or motion-blurred
    # faces give unreliable encodings. Skipped faces are counted (total_low_quality_faces),
    # never matched and never counted as unregistered. 0 disables a check.
    FACE_MIN_SIZE_PIXELS: int = 50
    FACE_MIN_SHARPNESS: float = 15.0
    # dlib re-sampling for enrollment photos (one-off, so it can afford far more than the
    # camera path): averages small distortions into a steadier reference encoding.
    FACE_ENROLLMENT_JITTERS: int = 10
    # dlib re-sampling per face encoding (face_recognition default 1). 0 roughly halves the
    # encoding time on CPUs; measure accuracy before changing it (applies to cameras only).
    FACE_ENCODING_JITTERS: int = 1
    # dlib upsampling before HOG face detection (cameras only). 1 = faces from ~40 px; 2 finds
    # ~20 px faces (distant CCTV) at about 4x the detection cost. Measure before changing.
    FACE_DETECTION_UPSAMPLE: int = 1
    # Seconds between reloads of enrolled face encodings by running workers.
    FACE_RELOAD_INTERVAL_SECONDS: float = 30.0

    # Attendance decisions. Recognition confidence = (1 - distance) * 100, so the
    # default 70 requires distance <= 0.30 (stricter than FACE_MATCH_TOLERANCE).
    # Calibrate on real footage before changing; do not lower just to get more matches.
    ATTENDANCE_MIN_CONFIDENCE: float = 70.0
    ATTENDANCE_MIN_CONSECUTIVE_FRAMES: int = 5
    ATTENDANCE_MAX_FRAME_GAP_SECONDS: float = 2.0  # resets the consecutive-frame count
    ATTENDANCE_ENTRY_COOLDOWN_SECONDS: float = 300.0  # min time between ENTRY events
    ATTENDANCE_EXIT_COOLDOWN_SECONDS: float = 60.0  # min time after EXIT before re-ENTRY
    ATTENDANCE_MIN_PRESENCE_SECONDS: float = 60.0  # min time inside before EXIT
    # A student is only reported ABSENT when the classroom's IN camera delivered frames for
    # the whole period apart from at most this many minutes (late start, short outage);
    # otherwise unseen students are UNVERIFIED, never absent.
    ATTENDANCE_MAX_COVERAGE_GAP_MINUTES: float = 5.0
    # Local time "HH:MM"; a first ENTRY after this counts as late in daily stats.
    # Unset = no lateness tracking (late is always 0).
    ATTENDANCE_LATE_AFTER: Optional[str] = None

    # Unregistered-person occupancy (services.UnknownOccupancyTracker; in memory only,
    # resets on server restart). Two unknown face encodings within this L2 distance are
    # treated as the same person, e.g. when matching an OUT sighting to someone inside.
    # Its frame, gap, exit-cooldown and min-presence thresholds reuse the ATTENDANCE_* values.
    UNKNOWN_SAME_PERSON_DISTANCE: float = 0.6
    # Stricter distance used when a face recognised as a registered student removes the
    # nearest unknown occupant/candidate (UnknownOccupancyTracker.resolve_known). Must not be
    # larger than UNKNOWN_SAME_PERSON_DISTANCE. PROVISIONAL: a conservative starting point,
    # not a validated biometric threshold - calibrate it with the real cameras.
    UNKNOWN_RESOLVE_KNOWN_DISTANCE: float = 0.45

    # Models. Relative paths are relative to the backend folder. Models are never
    # downloaded at runtime: a missing file is reported as a setup error.
    YOLO_MODEL_PATH: str = "yolov8n.pt"

    # Camera reliability
    CAMERA_AUTO_RECONNECT: bool = True  # reopen live sources (webcam/RTSP) after they drop, with backoff
    CAMERA_RECONNECT_MAX_DELAY_SECONDS: float = 30.0
    CAMERA_STALE_AFTER_SECONDS: float = 5.0  # no new frame for this long -> feed reported as stale
    # Live feeds: read continuously and always process the newest frame, so slow recognition
    # never falls behind real time (old buffered frames are skipped and counted).
    CAMERA_LATEST_FRAME_ONLY: bool = True
    # Network streams (rtsp/http): give up opening after this long; a read that stalls this
    # long counts as a failed read (then disconnect/reconnect handling applies)
    CAMERA_OPEN_TIMEOUT_SECONDS: float = 10.0
    CAMERA_READ_TIMEOUT_SECONDS: float = 5.0
    # Video-file camera sources must be inside this folder (relative to backend/), so the
    # API cannot be used to open arbitrary files on the server.
    VIDEO_FILES_DIR: str = "data/videos"

    # Period attendance policy (see app/attendance_logic.py)
    ATTENDANCE_DEFAULT_GRACE_MINUTES: int = 10  # arriving later than start + grace = LATE
    ATTENDANCE_EARLY_ARRIVAL_MINUTES: int = 15  # an ENTRY this long before start counts for the period
    ATTENDANCE_EARLY_DEPARTURE_MINUTES: int = 10  # leaving earlier than end - this = EARLY_DEPARTURE

    # Accounts. Self-registered accounts are always created with the read-only "viewer"
    # role; an admin promotes them (scripts/manage_users.py or /auth/users).
    # Off unless explicitly enabled: a missing setting must never open public sign-up.
    ALLOW_SELF_REGISTRATION: bool = False

    # Comma-separated origins allowed to call the API from a browser. localhost and
    # 127.0.0.1 are different origins to a browser, so both are listed.
    CORS_ORIGINS: str = ("http://localhost:3000,http://localhost:3001,"
                         "http://127.0.0.1:3000,http://127.0.0.1:3001")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        # Ignore extra fields from .env that aren't in the model
        extra = "ignore"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        environment = (self.ENVIRONMENT or "").strip().lower()
        if environment not in ALLOWED_ENVIRONMENTS:
            raise ValueError(
                f"ENVIRONMENT must be set to one of {', '.join(ALLOWED_ENVIRONMENTS)} "
                f"(got {self.ENVIRONMENT!r}). Set it in the environment or backend/.env."
            )
        self.ENVIRONMENT = environment

        if self.is_production:
            if self.DEBUG:
                raise ValueError("DEBUG must be False when ENVIRONMENT=production")
            if self.ENABLE_SYNTHETIC_CAMERA:
                raise ValueError("ENABLE_SYNTHETIC_CAMERA must be False when ENVIRONMENT=production")

        key = self.SECRET_KEY.get_secret_value().strip()
        if key in INSECURE_SECRET_KEYS or len(key) < MIN_SECRET_KEY_LENGTH:
            reason = "missing" if not key else ("a placeholder" if key in INSECURE_SECRET_KEYS else "too short")
            if self.is_production:
                # Fail fast in production: never sign tokens with a guessable key
                raise ValueError(f"SECRET_KEY is {reason}. {SECRET_KEY_HELP}")
            logger.warning(f"SECRET_KEY is {reason} (ENVIRONMENT={environment}); using a random key for this "
                           f"process. Logins will not survive a restart or work across workers. {SECRET_KEY_HELP}")
            self.SECRET_KEY = SecretStr(secrets.token_urlsafe(64))

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def cors_origins(self) -> list:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

settings = Settings()