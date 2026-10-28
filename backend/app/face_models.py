"""
Face recognition models: which one is active, how its encodings are stored and compared,
and the OpenCV SFace model (YuNet detector + SFace recognizer, opencv_zoo, Apache-2.0).

Two models are supported (settings.FACE_RECOGNITION_MODEL):
  "sface" (default)  OpenCV YuNet detector + SFace recognizer (ONNX files in backend/models/face).
                     Encodings are 128 float32 values normalised to unit length; compared by
                     cosine distance (1 - cosine similarity). Much more robust than dlib on
                     angled, small and low-light faces.
  "dlib"             The original face_recognition/dlib model: 128 float64 values compared by
                     Euclidean distance.

Storage (face_embeddings.embedding_data) identifies the model by its byte length, so the
two can never be compared with each other:  dlib = 128 x float64 = 1024 bytes,
sface = 128 x float32 = 512 bytes. Encodings of the inactive model are kept but ignored
(switching back needs no re-enrollment for those photos).

Confidence (0-100) is (1 - distance) * 100 for both models: for SFace that is cosine
similarity x 100. It is a match score, not a probability, and the two scales differ, so
each model has its own match and attendance thresholds (see thresholds()).
"""
import logging
import threading
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

DLIB, SFACE = "dlib", "sface"
MODELS = (DLIB, SFACE)
_DTYPES = {DLIB: np.float64, SFACE: np.float32}
_SIZES = {DLIB: 128 * 8, SFACE: 128 * 4}


class FaceModelUnavailable(RuntimeError):
    """The active model's files are missing or cannot be loaded"""


def active_model() -> str:
    model = (settings.FACE_RECOGNITION_MODEL or SFACE).strip().lower()
    if model not in MODELS:
        raise ValueError(f"FACE_RECOGNITION_MODEL must be one of {MODELS}, not {model!r}")
    return model


def thresholds(model: Optional[str] = None) -> Dict[str, float]:
    """Match tolerance (distance), attendance confidence and unregistered-tracking distances
    for one model. The tracker compares encodings by Euclidean distance; for unit-length
    SFace encodings L2 = sqrt(2 * cosine distance), so its limits are converted."""
    model = model or active_model()
    if model == DLIB:
        return {"match_tolerance": settings.FACE_MATCH_TOLERANCE,
                "min_confidence": settings.ATTENDANCE_MIN_CONFIDENCE,
                "enroll_lookalike": settings.FACE_MATCH_TOLERANCE,
                "unknown_same_person": settings.UNKNOWN_SAME_PERSON_DISTANCE,
                "unknown_resolve_known": settings.UNKNOWN_RESOLVE_KNOWN_DISTANCE}
    match = settings.FACE_SFACE_MATCH_DISTANCE
    resolve = 1.0 - settings.FACE_SFACE_MIN_CONFIDENCE / 100.0  # as strict as recording attendance
    return {"match_tolerance": match,
            "min_confidence": settings.FACE_SFACE_MIN_CONFIDENCE,
            # enrollment: refuse a photo within this distance of ANOTHER student (see config)
            "enroll_lookalike": 1.0 - settings.FACE_SFACE_LOOKALIKE_SIMILARITY,
            "unknown_same_person": float(np.sqrt(2.0 * match)),
            "unknown_resolve_known": float(np.sqrt(2.0 * min(resolve, match)))}


def encode(model: str, vector: np.ndarray) -> bytes:
    vector = np.asarray(vector, dtype=_DTYPES[model]).reshape(-1)
    if vector.shape != (128,):
        raise ValueError(f"expected 128 values, got {vector.size}")
    return vector.tobytes()


def encoded_size(model: Optional[str] = None) -> int:
    """Stored byte size of one encoding of this model (default: the active one)"""
    return _SIZES[model or active_model()]


def model_of(data: bytes) -> Optional[str]:
    """Which model produced stored encoding bytes (None = unrecognised format)"""
    size = len(data or b"")
    return next((m for m, n in _SIZES.items() if n == size), None)


def decode(data: bytes) -> Tuple[Optional[str], Optional[np.ndarray]]:
    """(model, float64 vector) of stored encoding bytes; (None, None) if unrecognised"""
    model = model_of(data)
    if model is None:
        return None, None
    return model, np.frombuffer(data, dtype=_DTYPES[model]).astype(np.float64)


def distances(model: str, known: List[np.ndarray], encoding: np.ndarray) -> np.ndarray:
    """Distance from one encoding to each known one: Euclidean for dlib, cosine distance
    (1 - cosine similarity) for SFace. Lower = more similar for both."""
    if len(known) == 0:
        return np.empty((0,))
    known = np.asarray(known, dtype=np.float64)
    encoding = np.asarray(encoding, dtype=np.float64)
    if model == SFACE:
        norms = np.linalg.norm(known, axis=1) * max(float(np.linalg.norm(encoding)), 1e-12)
        return 1.0 - (known @ encoding) / np.maximum(norms, 1e-12)
    return np.linalg.norm(known - encoding, axis=1)


def _model_path(path: str) -> str:
    from app.services import resolve_model_path
    return resolve_model_path(path)


class SFaceModel:
    """YuNet face detector + SFace recognizer. OpenCV's networks are not thread-safe, so
    each camera worker builds its own instance; calls on one instance are serialised."""

    def __init__(self, detector_path: Optional[str] = None, recognizer_path: Optional[str] = None,
                 score_threshold: Optional[float] = None):
        self.detector_path = _model_path(detector_path or settings.FACE_SFACE_DETECTOR_PATH)
        self.recognizer_path = _model_path(recognizer_path or settings.FACE_SFACE_RECOGNIZER_PATH)
        self.score_threshold = settings.FACE_SFACE_DETECTION_SCORE if score_threshold is None else score_threshold
        self._detector = None
        self._recognizer = None
        self._lock = threading.Lock()

    def _load(self):
        if self._detector is not None:
            return
        import os
        missing = [p for p in (self.detector_path, self.recognizer_path) if not os.path.isfile(p)]
        if missing:
            raise FaceModelUnavailable(
                "SFace model file(s) missing: " + ", ".join(os.path.basename(p) for p in missing)
                + ". See README, 'Model files' (backend/models/face), or set FACE_RECOGNITION_MODEL=dlib.")
        try:
            self._detector = cv2.FaceDetectorYN.create(self.detector_path, "", (320, 320), self.score_threshold, 0.3, 5000)
            self._recognizer = cv2.FaceRecognizerSF.create(self.recognizer_path, "")
        except cv2.error as e:
            self._detector = self._recognizer = None
            raise FaceModelUnavailable(f"Could not load the SFace models: {e}") from e

    def check(self):
        """Raise FaceModelUnavailable unless both model files load"""
        with self._lock:
            self._load()

    def detect(self, bgr: np.ndarray) -> List[np.ndarray]:
        """Faces in a BGR image: YuNet rows (x, y, w, h, 5 landmarks, score), best first"""
        if bgr is None or bgr.size == 0 or bgr.shape[0] < 10 or bgr.shape[1] < 10:
            return []
        with self._lock:
            self._load()
            self._detector.setInputSize((bgr.shape[1], bgr.shape[0]))
            _, faces = self._detector.detect(np.ascontiguousarray(bgr))
        if faces is None:
            return []
        return sorted(list(faces), key=lambda f: -float(f[-1]))

    @staticmethod
    def location(face: np.ndarray, offset: Tuple[int, int] = (0, 0)) -> Tuple[int, int, int, int]:
        """YuNet row -> (top, right, bottom, left), like face_recognition, plus an (x, y) offset"""
        x, y, w, h = (int(round(float(v))) for v in face[:4])
        ox, oy = offset
        return (y + oy, x + w + ox, y + h + oy, x + ox)

    def embed(self, bgr: np.ndarray, face: np.ndarray) -> np.ndarray:
        """Aligned SFace encoding of one detected face, normalised to unit length (float64)"""
        with self._lock:
            self._load()
            aligned = self._recognizer.alignCrop(np.ascontiguousarray(bgr), face)
            feature = self._recognizer.feature(aligned).reshape(-1).astype(np.float64)
        norm = float(np.linalg.norm(feature))
        return feature / norm if norm > 0 else feature
