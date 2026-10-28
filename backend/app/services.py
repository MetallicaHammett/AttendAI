"""
AI Services for AttendAI - Computer Vision Pipeline
"""

import os

# On-premise operation: Ultralytics probes the internet (DNS) on import and sends usage
# analytics when it believes it is online. YOLO_OFFLINE makes it skip both, without
# touching the user's global Ultralytics settings file. Must be set before any
# ultralytics import. An explicit YOLO_OFFLINE=0 in the environment is respected.
os.environ.setdefault("YOLO_OFFLINE", "1")
# RTSP over TCP: OpenCV's FFmpeg backend defaults to UDP, which on a busy campus network
# drops packets and produces smeared/grey frames. Read by OpenCV each time a stream is
# opened; an explicit OPENCV_FFMPEG_CAPTURE_OPTIONS in the environment is respected.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import cv2
import numpy as np


import collections
import copy
import logging
import math
import threading
import time
from typing import Callable, Iterable, List, Tuple, Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CameraOpenError(RuntimeError):
    """Raised when a camera/video source cannot be opened."""


class ModelUnavailableError(RuntimeError):
    """Raised when a required local model file is missing (never downloaded automatically)."""


from app.sources import mask_source  # noqa: E402  (re-exported for callers of services)


def resolve_model_path(path: str) -> str:
    """Absolute path of a model file; relative paths are relative to the backend folder"""
    return path if os.path.isabs(path) else os.path.join(BACKEND_DIR, path)


class FaceObservationKind(Enum):
    """What face matching concluded about one face. Input contract of UnknownOccupancyTracker.

    The caller must classify each face before calling the tracker:
      KNOWN     - matched one enrolled student (any confidence).
      AMBIGUOUS - close to enrolled encodings but not attributable to one student (two
                  students within the ambiguity margin). Probably a registered person, so it
                  must never be counted as unregistered.
      UNKNOWN   - definitely unregistered: no enrolled encoding within the match tolerance,
                  or no faces are enrolled at all.
    Faces that could not be detected at all never reach this point (DetectionStatus.NO_FACE).
    Only UNKNOWN changes tracker state; KNOWN and AMBIGUOUS are ignored by observe().
    """
    KNOWN = "known"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


@dataclass
class FaceMatch:
    """Classification of one face encoding against the enrolled faces.

    distance is the L2 distance to the best student's closest encoding (None when nobody is
    enrolled). confidence = (1 - distance) * 100 clipped to [0, 100] for KNOWN faces, else 0.
    It is a monotonic match score, NOT a calibrated probability."""
    kind: FaceObservationKind
    student_id: Optional[int]
    distance: Optional[float]
    confidence: float
    runner_up_distance: Optional[float] = None


class DetectionStatus(Enum):
    SUCCESS = "success"
    NO_PERSON = "no_person"
    NO_FACE = "no_face"
    UNKNOWN_PERSON = "unknown_person"
    PROCESSING_ERROR = "processing_error"

# Global flags for AI dependency availability
_TORCH_AVAILABLE = None
_TORCHVISION_AVAILABLE = None
_ULTRALYTICS_AVAILABLE = None
_DEEPSORT_AVAILABLE = None
_TORCH_IMPORT_ERROR = None
_TORCHVISION_IMPORT_ERROR = None
_ULTRALYTICS_IMPORT_ERROR = None
_DEEPSORT_IMPORT_ERROR = None

def _safe_import_torch():
    global _TORCH_AVAILABLE, _TORCH_IMPORT_ERROR
    if _TORCH_AVAILABLE is None:
        try:
            import torch
            _TORCH_AVAILABLE = True
        except Exception as e:
            _TORCH_AVAILABLE = False
            _TORCH_IMPORT_ERROR = str(e)
    return _TORCH_AVAILABLE

def _safe_import_torchvision():
    global _TORCHVISION_AVAILABLE, _TORCHVISION_IMPORT_ERROR
    if _TORCHVISION_AVAILABLE is None:
        try:
            import torchvision
            _TORCHVISION_AVAILABLE = True
        except Exception as e:
            _TORCHVISION_AVAILABLE = False
            _TORCHVISION_IMPORT_ERROR = str(e)
    return _TORCHVISION_AVAILABLE

def _safe_import_ultralytics():
    global _ULTRALYTICS_AVAILABLE, _ULTRALYTICS_IMPORT_ERROR
    if _ULTRALYTICS_AVAILABLE is None:
        try:
            from ultralytics import YOLO
            _ULTRALYTICS_AVAILABLE = True
        except Exception as e:
            _ULTRALYTICS_AVAILABLE = False
            _ULTRALYTICS_IMPORT_ERROR = str(e)
    return _ULTRALYTICS_AVAILABLE

def _safe_import_deepsort():
    global _DEEPSORT_AVAILABLE, _DEEPSORT_IMPORT_ERROR
    if _DEEPSORT_AVAILABLE is None:
        # deep-sort-realtime 1.0 still uses np.float, removed in NumPy 1.24 (every update then
        # failed with "module 'numpy' has no attribute 'float'"). np.float was always an alias
        # of the builtin float, so restoring it changes no behaviour.
        if "float" not in np.__dict__:
            np.float = float
        try:
            from deep_sort_realtime.deepsort_tracker import DeepSort
            _DEEPSORT_AVAILABLE = True
        except Exception as e:
            _DEEPSORT_AVAILABLE = False
            _DEEPSORT_IMPORT_ERROR = str(e)
    return _DEEPSORT_AVAILABLE

@dataclass
class DetectionResult:
    """Result from person detection"""
    boxes: List[Tuple[int, int, int, int]]  # (x1, y1, x2, y2)
    confidences: List[float]
    status: DetectionStatus
    frame_shape: Tuple[int, int, int]  # (height, width, channels)

@dataclass
class FaceResult:
    """Result from face detection and recognition"""
    face_locations: List[Tuple[int, int, int, int]]  # (top, right, bottom, left)
    face_encodings: List[np.ndarray]
    status: DetectionStatus
    person_boxes: List[Tuple[int, int, int, int]]  # Corresponding person boxes
    # Faces found but too small or blurred to identify reliably: never matched, never
    # counted as unregistered (treated like a face turned away)
    low_quality_count: int = 0

@dataclass
class RecognitionResult:
    """Result from face recognition"""
    student_ids: List[Optional[int]]  # None for unknown AND ambiguous faces - see kinds
    confidences: List[float]  # Match score (0-100), see FaceMatch
    status: DetectionStatus
    face_locations: List[Tuple[int, int, int, int]]
    # Per face: KNOWN / UNKNOWN / AMBIGUOUS (same order as student_ids)
    kinds: List[FaceObservationKind] = field(default_factory=list)

@dataclass
class TrackingResult:
    """Result from multi-object tracking"""
    tracks: List[Dict[str, Any]]  # List of track dictionaries
    status: DetectionStatus

@dataclass
class AttendanceDecision:
    """Result from attendance engine"""
    student_id: Optional[int]
    confidence: float
    event_type: str  # ENTRY or EXIT
    timestamp: float
    should_record: bool
    reasoning: str
    camera_id: Optional[int] = None

class PersonDetectionService:
    """Person detection using YOLOv8 with lazy loading"""

    def __init__(self, model_path: str = "yolov8n.pt", confidence_threshold: float = 0.5):
        self.confidence_threshold = confidence_threshold
        # Relative paths are resolved against the backend folder, not the process cwd
        self.model_path = resolve_model_path(model_path)
        self.model = None
        self._model_loaded = False
        self._load_error = None

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    def check_model_file(self):
        """Raise ModelUnavailableError if the model file is missing. Ultralytics would
        otherwise try to download it from the internet."""
        if not os.path.isfile(self.model_path):
            raise ModelUnavailableError(
                f"Person-detection model not found at {self.model_path}. AttendAI never downloads "
                f"models at runtime: copy yolov8n.pt there (see README, 'Model files') or set "
                f"YOLO_MODEL_PATH.")

    def _load_model(self):
        """Lazy load YOLO model"""
        if self._model_loaded:
            return self.model is not None

        try:
            self.check_model_file()
        except ModelUnavailableError as e:
            self._load_error = str(e)
            self._model_loaded = True
            logger.error(self._load_error)
            return False

        if not _safe_import_ultralytics():
            self._load_error = _ULTRALYTICS_IMPORT_ERROR
            # Don't log the error here to avoid spam - it will be logged when actually used
            return False

        try:
            from ultralytics import YOLO
            self.model = YOLO(self.model_path)
            self._model_loaded = True
            logger.info(f"Loaded YOLO model from {self.model_path}")
            return True
        except Exception as e:
            self._load_error = str(e)
            logger.error(f"Failed to load YOLO model: {e}")
            self._model_loaded = True  # Mark as attempted to avoid repeated attempts
            return False

    def detect_persons(self, frame: np.ndarray) -> DetectionResult:
        """Detect persons in a frame"""
        # Try to load model if not already loaded
        if not self._load_model():
            return DetectionResult(
                boxes=[],
                confidences=[],
                status=DetectionStatus.PROCESSING_ERROR,
                frame_shape=frame.shape
            )

        try:
            # Run YOLO inference
            results = self.model(frame, verbose=False)

            boxes = []
            confidences = []

            for result in results:
                boxes_tensor = result.boxes.xyxy  # (x1, y1, x2, y2)
                conf_tensor = result.boxes.conf   # Confidence scores
                cls_tensor = result.boxes.cls     # Class indices

                # Filter for person class (class 0 in COCO dataset)
                person_mask = (cls_tensor == 0) & (conf_tensor >= self.confidence_threshold)

                if person_mask.any():
                    person_boxes = boxes_tensor[person_mask].cpu().numpy()
                    person_confs = conf_tensor[person_mask].cpu().numpy()

                    for box, conf in zip(person_boxes, person_confs):
                        boxes.append(tuple(map(int, box)))  # Convert to int tuples
                        confidences.append(float(conf))

            if len(boxes) == 0:
                return DetectionResult(
                    boxes=[],
                    confidences=[],
                    status=DetectionStatus.NO_PERSON,
                    frame_shape=frame.shape
                )

            return DetectionResult(
                boxes=boxes,
                confidences=confidences,
                status=DetectionStatus.SUCCESS,
                frame_shape=frame.shape
            )

        except Exception as e:
            logger.error(f"Person detection error: {e}")
            return DetectionResult(
                boxes=[],
                confidences=[],
                status=DetectionStatus.PROCESSING_ERROR,
                frame_shape=frame.shape
            )

class FaceDetectionService:
    """Face detection and recognition service"""

    def __init__(self,
                 model_name: str = "hog",
                 confidence_threshold: float = 0.6,
                 match_tolerance: float = 0.6,
                 ambiguity_margin: float = 0.0,
                 num_jitters: int = 1,
                 upsample: int = 1,
                 min_face_size: int = 0,
                 min_sharpness: float = 0.0,
                 recognition_model: str = "dlib",
                 sface_model=None):
        from app import face_models
        if recognition_model not in face_models.MODELS:
            raise ValueError(f"Unknown recognition model {recognition_model!r}")
        # "dlib" (face_recognition) or "sface" (OpenCV YuNet + SFace); see app/face_models.py.
        # Known encodings must come from the same model (load_known_faces_from_db filters).
        self.recognition_model = recognition_model
        self.sface = (sface_model or face_models.SFaceModel()) if recognition_model == face_models.SFACE else None
        self.model_name = model_name  # 'hog' or 'cnn' (dlib only)
        # Faces smaller (shorter side, px) or blurrier (variance of the Laplacian) than this are
        # skipped instead of matched: their encodings are unreliable. 0 = no check.
        self.min_face_size = min_face_size
        self.min_sharpness = min_sharpness
        # dlib upsampling before face detection (face_recognition default 1). HOG finds faces
        # down to roughly 40 px at 1 and ~20 px at 2, at about 4x the detection cost.
        self.upsample = upsample
        # dlib re-samples each face this many times when encoding (face_recognition's
        # default is 1). 0 is about twice as fast; the effect on accuracy must be measured.
        self.num_jitters = num_jitters
        self.confidence_threshold = confidence_threshold
        # Max Euclidean distance between 128-d encodings for a candidate match
        self.match_tolerance = match_tolerance
        # Runner-up different student within this distance of the best => ambiguous
        self.ambiguity_margin = ambiguity_margin
        self.known_face_encodings = []  # Will be populated from database
        self.known_face_student_ids = []  # Corresponding student IDs
        # Guards swapping the known-face lists while a worker is matching
        self._known_lock = threading.Lock()

    def load_known_faces(self, face_embeddings: List[Tuple[int, np.ndarray]]):
        """Load known face embeddings from database (atomic replace, thread-safe)"""
        encodings = [np.asarray(emb, dtype=np.float64) for _, emb in face_embeddings]
        student_ids = [student_id for student_id, _ in face_embeddings]
        with self._known_lock:
            self.known_face_encodings = encodings
            self.known_face_student_ids = student_ids
        logger.info(f"Loaded {len(encodings)} known face encodings")

    def detect_faces(self, frame: np.ndarray, person_boxes: List[Tuple[int, int, int, int]]) -> FaceResult:
        """Detect faces within person regions"""
        if self.sface is not None:
            return self._detect_faces_sface(frame, person_boxes)
        try:
            if len(person_boxes) == 0:
                return FaceResult(
                    face_locations=[],
                    face_encodings=[],
                    status=DetectionStatus.NO_PERSON,
                    person_boxes=[]
                )

            all_face_locations = []
            all_face_encodings = []
            valid_person_boxes = []
            low_quality = 0

            # Process each person box
            for person_box in person_boxes:
                x1, y1, x2, y2 = person_box

                # Extract person region
                person_roi = frame[y1:y2, x1:x2]

                if person_roi.size == 0:
                    continue

                import face_recognition
                # Convert BGR to RGB for face_recognition (OpenCV uses BGR by default)
                rgb_person_roi = cv2.cvtColor(person_roi, cv2.COLOR_BGR2RGB)
                # Detect faces in the person ROI
                face_locations = face_recognition.face_locations(
                    rgb_person_roi,
                    number_of_times_to_upsample=self.upsample,
                    model=self.model_name
                )

                if len(face_locations) == 0:
                    continue

                # Quality gate before encoding (also saves the encoding cost of skipped faces)
                usable = [loc for loc in face_locations if self.face_is_usable(rgb_person_roi, loc)]
                low_quality += len(face_locations) - len(usable)
                face_locations = usable
                if len(face_locations) == 0:
                    continue

                import face_recognition
                # Get face encodings
                face_encodings = face_recognition.face_encodings(
                    rgb_person_roi,
                    face_locations,
                    num_jitters=self.num_jitters
                )

                # Convert face locations from ROI coordinates to frame coordinates
                for i, (top, right, bottom, left) in enumerate(face_locations):
                    # Adjust coordinates to frame space
                    frame_top = top + y1
                    frame_right = right + x1
                    frame_bottom = bottom + y1
                    frame_left = left + x1

                    all_face_locations.append((frame_top, frame_right, frame_bottom, frame_left))
                    all_face_encodings.append(face_encodings[i])
                    valid_person_boxes.append(person_box)

            if len(all_face_locations) == 0:
                return FaceResult(
                    face_locations=[],
                    face_encodings=[],
                    status=DetectionStatus.NO_FACE,
                    person_boxes=person_boxes,
                    low_quality_count=low_quality
                )

            return FaceResult(
                face_locations=all_face_locations,
                face_encodings=all_face_encodings,
                status=DetectionStatus.SUCCESS,
                person_boxes=valid_person_boxes,
                low_quality_count=low_quality
            )

        except Exception as e:
            logger.error(f"Face detection error: {e}")
            return FaceResult(
                face_locations=[],
                face_encodings=[],
                status=DetectionStatus.PROCESSING_ERROR,
                person_boxes=person_boxes
            )

    def _detect_faces_sface(self, frame: np.ndarray, person_boxes: List[Tuple[int, int, int, int]]) -> FaceResult:
        """Same contract as detect_faces, with YuNet detection and aligned SFace encodings.
        A missing/broken model file is a PROCESSING_ERROR (never silently 'no face')."""
        if len(person_boxes) == 0:
            return FaceResult(face_locations=[], face_encodings=[], status=DetectionStatus.NO_PERSON, person_boxes=[])
        locations, encodings, boxes, low_quality = [], [], [], 0
        try:
            for person_box in person_boxes:
                x1, y1, x2, y2 = person_box
                roi = frame[max(y1, 0):y2, max(x1, 0):x2]
                if roi.size == 0:
                    continue
                rgb_roi = None
                for face in self.sface.detect(roi):
                    local = self.sface.location(face)
                    if self.min_face_size > 0 or self.min_sharpness > 0:
                        rgb_roi = rgb_roi if rgb_roi is not None else cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
                        if not self.face_is_usable(rgb_roi, local):
                            low_quality += 1
                            continue
                    encodings.append(self.sface.embed(roi, face))
                    locations.append(self.sface.location(face, offset=(max(x1, 0), max(y1, 0))))
                    boxes.append(person_box)
        except Exception as e:
            logger.error(f"Face detection error (SFace): {type(e).__name__}: {e}")
            return FaceResult(face_locations=[], face_encodings=[], status=DetectionStatus.PROCESSING_ERROR,
                              person_boxes=person_boxes, low_quality_count=low_quality)
        status = DetectionStatus.SUCCESS if locations else DetectionStatus.NO_FACE
        return FaceResult(face_locations=locations, face_encodings=encodings, status=status,
                          person_boxes=boxes if locations else person_boxes, low_quality_count=low_quality)

    @staticmethod
    def face_quality(rgb_image: np.ndarray, location) -> Tuple[float, float]:
        """(shorter side of the face box in px, sharpness = variance of the Laplacian)"""
        top, right, bottom, left = location
        crop = rgb_image[max(top, 0):max(bottom, 0), max(left, 0):max(right, 0)]
        if crop.size == 0:
            return 0.0, 0.0
        grey = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        return float(min(bottom - top, right - left)), float(cv2.Laplacian(grey, cv2.CV_64F).var())

    def face_is_usable(self, rgb_image: np.ndarray, location) -> bool:
        if self.min_face_size <= 0 and self.min_sharpness <= 0:
            return True
        size, sharpness = self.face_quality(rgb_image, location)
        return size >= self.min_face_size and sharpness >= self.min_sharpness

    @staticmethod
    def face_distances(known_encodings: List[np.ndarray], face_encoding: np.ndarray) -> np.ndarray:
        """Euclidean (L2) distance from one encoding to each known encoding.

        Identical to face_recognition.face_distance; computed with numpy so the
        matching logic does not depend on dlib being importable.
        """
        if len(known_encodings) == 0:
            return np.empty((0,))
        return np.linalg.norm(np.asarray(known_encodings, dtype=np.float64) - np.asarray(face_encoding, dtype=np.float64), axis=1)

    def classify_encoding(self,
                          face_encoding: np.ndarray,
                          known_encodings: List[np.ndarray],
                          known_student_ids: List[int]) -> FaceMatch:
        """Classify one encoding as KNOWN / UNKNOWN / AMBIGUOUS.

        - Each student's score is their closest enrolled encoding.
        - Nobody enrolled, or no student within match_tolerance -> UNKNOWN.
        - Runner-up *different* student also within match_tolerance and within
          ambiguity_margin of the best -> AMBIGUOUS (probably registered, identity unclear).
          Multiple encodings of the same student never make a match ambiguous.
        - Otherwise KNOWN with confidence = (1 - distance) * 100, clipped to [0, 100].
        """
        from app import face_models
        # Euclidean for dlib, cosine distance for SFace; both "lower = more similar"
        distances = face_models.distances(self.recognition_model, known_encodings, face_encoding)
        if distances.size == 0:
            return FaceMatch(FaceObservationKind.UNKNOWN, None, None, 0.0)

        best_per_student: Dict[int, float] = {}
        for sid, dist in zip(known_student_ids, distances):
            d = float(dist)
            if sid not in best_per_student or d < best_per_student[sid]:
                best_per_student[sid] = d

        ranked = sorted(best_per_student.items(), key=lambda kv: kv[1])
        best_id, best_dist = ranked[0]
        runner_up_dist = ranked[1][1] if len(ranked) > 1 else None
        if best_dist > self.match_tolerance:
            return FaceMatch(FaceObservationKind.UNKNOWN, None, best_dist, 0.0, runner_up_dist)
        if (runner_up_dist is not None and runner_up_dist <= self.match_tolerance
                and runner_up_dist - best_dist <= self.ambiguity_margin):
            return FaceMatch(FaceObservationKind.AMBIGUOUS, None, best_dist, 0.0, runner_up_dist)
        confidence = float(min(100.0, max(0.0, (1.0 - best_dist) * 100.0)))
        return FaceMatch(FaceObservationKind.KNOWN, best_id, best_dist, confidence, runner_up_dist)

    def match_encoding(self,
                       face_encoding: np.ndarray,
                       known_encodings: List[np.ndarray],
                       known_student_ids: List[int]) -> Tuple[Optional[int], float]:
        """Match one encoding. Returns (student_id or None, confidence 0-100).
        Compatibility wrapper around classify_encoding: UNKNOWN and AMBIGUOUS both
        return (None, 0.0) - use classify_encoding when the difference matters."""
        match = self.classify_encoding(face_encoding, known_encodings, known_student_ids)
        return match.student_id, match.confidence

    def recognize_faces(self, face_encodings: List[np.ndarray]) -> RecognitionResult:
        """Recognize faces against known database"""
        try:
            if len(face_encodings) == 0:
                return RecognitionResult(
                    student_ids=[],
                    confidences=[],
                    status=DetectionStatus.NO_FACE,
                    face_locations=[]
                )

            # Snapshot so a concurrent reload cannot change the lists mid-match
            with self._known_lock:
                known_encodings = list(self.known_face_encodings)
                known_student_ids = list(self.known_face_student_ids)

            if len(known_encodings) == 0:
                # No known faces in database: every detected face is unregistered
                return RecognitionResult(
                    student_ids=[None] * len(face_encodings),
                    confidences=[0.0] * len(face_encodings),
                    status=DetectionStatus.UNKNOWN_PERSON,
                    face_locations=[],  # Will be filled by caller
                    kinds=[FaceObservationKind.UNKNOWN] * len(face_encodings)
                )

            student_ids = []
            confidences = []
            kinds = []

            for face_encoding in face_encodings:
                match = self.classify_encoding(face_encoding, known_encodings, known_student_ids)
                student_ids.append(match.student_id)
                confidences.append(match.confidence)
                kinds.append(match.kind)

            # Determine overall status
            known_count = sum(1 for sid in student_ids if sid is not None)
            unknown_count = len(student_ids) - known_count

            if known_count == 0:
                status = DetectionStatus.UNKNOWN_PERSON
            elif unknown_count == 0:
                status = DetectionStatus.SUCCESS
            else:
                status = DetectionStatus.SUCCESS  # Mixed known/unknown

            return RecognitionResult(
                student_ids=student_ids,
                confidences=confidences,
                status=status,
                face_locations=[],  # Will be filled by caller
                kinds=kinds
            )

        except Exception as e:
            logger.error(f"Face recognition error: {e}")
            return RecognitionResult(
                student_ids=[],
                confidences=[],
                status=DetectionStatus.PROCESSING_ERROR,
                face_locations=[]
            )

class TrackingService:
    """Multi-object tracking using DeepSORT with lazy loading"""

    def __init__(self, max_age: int = 30, nms_max_overlap: float = 1.0):
        self.max_age = max_age
        self.nms_max_overlap = nms_max_overlap
        self.tracker = None
        self._tracker_initialized = False
        self._init_error = None

    def _initialize_tracker(self):
        """Lazy initialize DeepSORT tracker"""
        if self._tracker_initialized:
            return self.tracker is not None

        if not _safe_import_deepsort():
            self._init_error = _DEEPSORT_IMPORT_ERROR
            # Don't log the error here to avoid spam - it will be logged when actually used
            return False

        try:
            from deep_sort_realtime.deepsort_tracker import DeepSort
            self.tracker = DeepSort(
                max_age=self.max_age,
                nms_max_overlap=self.nms_max_overlap,
                max_cosine_distance=0.2,
                embedder=None,  # Built-in embedders need pkg_resources; we pass our own embeds
                nn_budget=None,
                override_track_class=None,
                half=False,
                bgr=True
            )
            self._tracker_initialized = True
            logger.info("Initialized DeepSORT tracker")
            return True
        except Exception as e:
            self._init_error = str(e)
            logger.error(f"Failed to initialize DeepSORT tracker: {e}")
            self._tracker_initialized = True  # Mark as attempted to avoid repeated attempts
            return False

    @staticmethod
    def _appearance_embedding(frame: np.ndarray, box: Tuple[int, int, int, int]) -> np.ndarray:
        """Cheap appearance feature for DeepSORT: L2-normalised HSV colour histogram of the crop."""
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (max(0, int(box[0])), max(0, int(box[1])), min(w, int(box[2])), min(h, int(box[3])))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return np.full(8 * 8 * 4, 1.0 / np.sqrt(8 * 8 * 4), dtype=np.float32)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 4], [0, 180, 0, 256, 0, 256]).flatten()
        norm = np.linalg.norm(hist)
        return (hist / norm if norm > 0 else hist).astype(np.float32)

    def update_tracks(self, frame: np.ndarray, detections: List[Tuple[int, int, int, int, float]]) -> TrackingResult:
        """Update tracks with new detections"""
        # Try to initialize tracker if not already initialized
        if not self._initialize_tracker():
            return TrackingResult(
                tracks=[],
                status=DetectionStatus.PROCESSING_ERROR
            )

        try:
            # Format detections for DeepSORT: [([left, top, w, h], confidence, class), ...]
            detections_for_tracker = []
            embeds = []
            for (x1, y1, x2, y2, conf) in detections:
                w = x2 - x1
                h = y2 - y1
                detections_for_tracker.append(([float(x1), float(y1), float(w), float(h)], float(conf), 'person'))
                embeds.append(self._appearance_embedding(frame, (x1, y1, x2, y2)))

            # Update tracker (embedder disabled, so appearance embeddings are supplied)
            tracks = self.tracker.update_tracks(detections_for_tracker, embeds=embeds, frame=frame)

            # Format results
            formatted_tracks = []
            for track in tracks:
                if not track.is_confirmed():
                    continue

                track_id = track.track_id
                ltrb = track.to_ltrb()  # left, top, right, bottom
                # Get class name and confidence if available
                class_name = getattr(track, 'det_class', 'person')
                confidence = getattr(track, 'det_conf', 1.0)

                formatted_tracks.append({
                    'track_id': track_id,
                    'bbox': tuple(map(int, ltrb)),  # (left, top, right, bottom)
                    'class': class_name,
                    'confidence': float(confidence)
                })

            return TrackingResult(
                tracks=formatted_tracks,
                status=DetectionStatus.SUCCESS if len(formatted_tracks) > 0 else DetectionStatus.NO_PERSON
            )

        except Exception as e:
            logger.error(f"Tracking error: {e}")
            return TrackingResult(
                tracks=[],
                status=DetectionStatus.PROCESSING_ERROR
            )

class AttendanceEngine:
    """Attendance decision engine (thread-safe; one instance is shared by all cameras).

    Per-student state machine:
        OUTSIDE --(IN camera, stable recognition)--> ENTRY --> INSIDE
        INSIDE  --(OUT camera, stable recognition, min presence)--> EXIT --> OUTSIDE

    - An OUT camera never produces an EXIT unless the student has an active ENTRY
      recorded on the same local day.
    - A student who is INSIDE never gets a second ENTRY; one who is OUTSIDE never
      gets an EXIT. Cooldowns additionally suppress flapping at the door.
    - Stability ("consecutive frames") is counted per student per camera and resets
      after a gap longer than max_frame_gap seconds.
    - Every check-and-update runs under one lock, and the optional persist callback
      runs inside it, so simultaneous cameras cannot both record for the same
      student. If persist raises, the student's state is rolled back.
    - Zones: a camera linked to a classroom passes zone=<classroom id>; inside/outside,
      cooldowns and stability are then tracked per (student, classroom), so being inside
      one classroom never blocks an ENTRY recorded by another classroom's camera. Cameras
      without a classroom use zone None (one building-wide state, the original behaviour).
    - Timestamps are wall-clock epoch seconds.
    """

    def __init__(self,
                 confidence_threshold: float = 70.0,
                 min_consecutive_frames: int = 5,
                 entry_cooldown: float = 300.0,  # 5 minutes between ENTRY events
                 exit_cooldown: float = 60.0,    # 1 minute after EXIT before re-ENTRY
                 min_presence_seconds: float = 60.0,  # time inside before EXIT allowed
                 max_frame_gap: float = 2.0):    # gap that breaks a consecutive run
        self.confidence_threshold = confidence_threshold
        self.min_consecutive_frames = min_consecutive_frames
        self.entry_cooldown = entry_cooldown
        self.exit_cooldown = exit_cooldown
        self.min_presence_seconds = min_presence_seconds
        self.max_frame_gap = max_frame_gap

        # State tracking: student_id (zone None) or (student_id, zone) -> state dict
        self.student_states = {}
        self.last_cleanup = 0
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @staticmethod
    def _key(student_id: int, zone: Any = None):
        return student_id if zone is None else (student_id, zone)

    @staticmethod
    def _student_of(key) -> int:
        return key[0] if isinstance(key, tuple) else key

    def _get_student_state(self, student_id: int, zone: Any = None) -> Dict[str, Any]:
        """Get or create state for a student in one zone (caller holds the lock)"""
        key = self._key(student_id, zone)
        if key not in self.student_states:
            self.student_states[key] = {
                'consecutive': {},       # camera key -> consecutive stable recognitions
                'last_seen': {},         # camera key -> last recognition timestamp
                'last_entry_time': None,
                'last_exit_time': None,
                'last_seen_time': 0,
                'current_status': 'UNKNOWN'  # ENTERED, EXITED, UNKNOWN
            }
        return self.student_states[key]

    @staticmethod
    def _same_local_day(a: float, b: float) -> bool:
        return time.localtime(a)[:3] == time.localtime(b)[:3]

    def _is_inside(self, state: Dict[str, Any], now: float) -> bool:
        """True if the student has an active ENTRY from the same local day"""
        return (state['current_status'] == 'ENTERED'
                and state['last_entry_time'] is not None
                and self._same_local_day(state['last_entry_time'], now))

    def get_consecutive(self, student_id: int, camera_key: Any, zone: Any = None) -> int:
        with self._lock:
            state = self.student_states.get(self._key(student_id, zone))
            return state['consecutive'].get(camera_key, 0) if state else 0

    def inside_count(self, now: Optional[float] = None) -> int:
        """Number of distinct known students currently inside anywhere (active entry today)"""
        now = time.time() if now is None else now
        with self._lock:
            return len({self._student_of(k) for k, s in self.student_states.items() if self._is_inside(s, now)})

    def seed_from_events(self, events: Iterable[Tuple]):
        """Restore state from persisted events: iterable of (student_id, event_type, epoch_ts)
        or (student_id, event_type, epoch_ts, zone).

        Events older than what the engine already knows for a student are ignored,
        so this is safe to call again when another camera starts.
        """
        with self._lock:
            for event in sorted(events, key=lambda e: e[2]):
                student_id, event_type, ts = event[:3]
                zone = event[3] if len(event) > 3 else None
                state = self._get_student_state(student_id, zone)
                known = [t for t in (state['last_entry_time'], state['last_exit_time']) if t is not None]
                if known and ts <= max(known):
                    continue
                if event_type == "ENTRY":
                    state['last_entry_time'] = ts
                    state['current_status'] = 'ENTERED'
                elif event_type == "EXIT":
                    state['last_exit_time'] = ts
                    state['current_status'] = 'EXITED'

    def _cleanup_old_states(self, current_time: float):
        """Clean up old student states to prevent memory leak (caller holds the lock)"""
        if current_time - self.last_cleanup < 3600:  # Cleanup every hour
            return

        cutoff_time = current_time - (self.entry_cooldown * 2)
        to_remove = []

        for student_id, state in self.student_states.items():
            if self._is_inside(state, current_time):
                continue  # Never forget an active entry, or the next sighting would duplicate it
            last_activity = max(state['last_seen_time'], state['last_entry_time'] or 0, state['last_exit_time'] or 0)
            if last_activity < cutoff_time:
                to_remove.append(student_id)

        for student_id in to_remove:
            del self.student_states[student_id]

        self.last_cleanup = current_time
        logger.debug(f"Cleaned up {len(to_remove)} old student states")

    def process_recognition(self,
                           student_id: Optional[int],
                           confidence: float,
                           timestamp: float,
                           camera_type: str = "IN",
                           camera_id: Optional[int] = None,
                           persist: Optional[Callable[[AttendanceDecision], None]] = None,
                           max_frame_gap: Optional[float] = None,
                           zone: Any = None) -> AttendanceDecision:
        """Process a recognition event and make attendance decision.

        If persist is given it is called (under the engine lock) for a decision
        that should be recorded; if it raises, state is rolled back and the
        returned decision has should_record=False.

        max_frame_gap (optional) widens the gap that breaks a consecutive run for this
        call; camera workers pass it when processing one frame takes longer than the
        configured gap, so slow frames (many faces on a CPU) cannot prevent confirmation.
        """
        def decide(event_type: str, should_record: bool, reasoning: str) -> AttendanceDecision:
            return AttendanceDecision(
                student_id=student_id,
                confidence=confidence,
                event_type=event_type,
                timestamp=timestamp,
                should_record=should_record,
                reasoning=reasoning,
                camera_id=camera_id
            )

        # Handle unknown persons
        if student_id is None:
            return decide("UNKNOWN", False, "Unknown person")

        with self._lock:
            # Cleanup old states periodically
            self._cleanup_old_states(timestamp)

            state = self._get_student_state(student_id, zone)
            state['last_seen_time'] = timestamp
            camera_key = camera_id if camera_id is not None else camera_type.upper()

            # A gap in sightings on this camera breaks the consecutive run
            last_seen = state['last_seen'].get(camera_key)
            gap_limit = max(self.max_frame_gap, max_frame_gap or 0.0)
            if last_seen is not None and timestamp - last_seen > gap_limit:
                state['consecutive'][camera_key] = 0
            state['last_seen'][camera_key] = timestamp

            # Check if confidence meets threshold
            if confidence < self.confidence_threshold:
                state['consecutive'][camera_key] = 0
                return decide("LOW_CONFIDENCE", False,
                              f"Confidence {confidence:.1f}% below threshold {self.confidence_threshold}%")

            consecutive = state['consecutive'].get(camera_key, 0) + 1
            state['consecutive'][camera_key] = consecutive
            if consecutive < self.min_consecutive_frames:
                return decide("STABILIZING", False,
                              f"Need {self.min_consecutive_frames - consecutive} more stable frames")

            inside = self._is_inside(state, timestamp)
            last_entry = state['last_entry_time']
            last_exit = state['last_exit_time']

            # IN camera -> ENTRY event, OUT camera -> EXIT event
            if camera_type.upper() == "IN":
                if inside:
                    return decide("NO_CHANGE", False, "IN camera: no entry - student already inside (active entry today)")
                if last_entry is not None and timestamp - last_entry < self.entry_cooldown:
                    return decide("NO_CHANGE", False,
                                  f"IN camera: no entry - {timestamp - last_entry:.1f}s since last entry (cooldown {self.entry_cooldown}s)")
                if last_exit is not None and timestamp - last_exit < self.exit_cooldown:
                    return decide("NO_CHANGE", False,
                                  f"IN camera: no entry - {timestamp - last_exit:.1f}s since exit (cooldown {self.exit_cooldown}s)")
                event_type, reasoning = "ENTRY", "Entry recorded via IN camera"
            else:
                if not inside:
                    return decide("NO_CHANGE", False,
                                  f"OUT camera: no exit - no active entry today (status={state['current_status']})")
                if timestamp - last_entry < self.min_presence_seconds:
                    return decide("NO_CHANGE", False,
                                  f"OUT camera: no exit - only {timestamp - last_entry:.1f}s since entry (min {self.min_presence_seconds}s)")
                event_type, reasoning = "EXIT", "Exit recorded via OUT camera"

            snapshot = copy.deepcopy(state)
            if event_type == "ENTRY":
                state['last_entry_time'] = timestamp
                state['current_status'] = 'ENTERED'
            else:
                state['last_exit_time'] = timestamp
                state['current_status'] = 'EXITED'
            state['consecutive'] = {}  # Reset after recording (all cameras)

            decision = decide(event_type, True, reasoning)
            if persist is not None:
                try:
                    persist(decision)
                except Exception as e:
                    self.student_states[self._key(student_id, zone)] = snapshot
                    logger.error(f"Failed to persist {event_type} for student {student_id}; state rolled back: {e}")
                    decision.should_record = False
                    decision.reasoning = f"Persistence failed: {e}"
            return decision


# One engine shared by every camera worker so IN/OUT cameras see the same state
_shared_engine: Optional[AttendanceEngine] = None
_shared_engine_lock = threading.Lock()


def get_shared_attendance_engine(**kwargs) -> AttendanceEngine:
    """Return the process-wide AttendanceEngine, creating it with kwargs on first use"""
    global _shared_engine
    with _shared_engine_lock:
        if _shared_engine is None:
            _shared_engine = AttendanceEngine(**kwargs)
        return _shared_engine


def get_shared_attendance_engine_if_exists() -> Optional[AttendanceEngine]:
    """The shared engine, or None if no camera has created it yet (does not create one)"""
    with _shared_engine_lock:
        return _shared_engine


def reset_shared_attendance_engine():
    """Drop the shared engine (tests / when no camera is running)"""
    global _shared_engine
    with _shared_engine_lock:
        _shared_engine = None


# Bumped whenever enrolled faces change so running workers reload them
_known_faces_version = 0
_known_faces_version_lock = threading.Lock()


def notify_known_faces_changed():
    global _known_faces_version
    with _known_faces_version_lock:
        _known_faces_version += 1


def get_known_faces_version() -> int:
    with _known_faces_version_lock:
        return _known_faces_version


@dataclass
class UnknownObservationResult:
    """Outcome of UnknownOccupancyTracker.observe()"""
    event: str  # IGNORED, CANDIDATE, ENTRY, EXIT, REFRESHED, NO_CHANGE
    inside_count: int
    occupant_id: Optional[int]
    reasoning: str


@dataclass
class _UnknownCandidate:
    encoding: np.ndarray  # running mean of this candidate's sightings
    samples: int
    consecutive: int
    last_seen: float


@dataclass
class _UnknownOccupant:
    occupant_id: int
    encoding: np.ndarray  # running mean, refreshed by later sightings
    samples: int
    entered_at: float
    last_seen: float
    camera_key: Any


class UnknownOccupancyTracker:
    """Estimated number of unregistered people inside, matched by face encoding (thread-safe).

    Face encodings are the only identity cue comparable across cameras (DeepSORT track IDs
    are per camera and per process), so the IN and OUT cameras reconcile people by L2
    distance between 128-d encodings: two encodings within same_person_distance are treated
    as the same person.

    Per UNKNOWN sighting at camera_key:
      - Sightings are grouped into a per-camera candidate. The candidate's consecutive count
        restarts when it has not been seen for more than max_frame_gap seconds.
      - Nothing happens until min_consecutive_frames consecutive sightings.
      - IN camera, confirmed: matches someone already inside -> REFRESHED (no new count);
        matches someone who exited less than exit_cooldown seconds ago -> NO_CHANGE;
        otherwise ENTRY (inside +1).
      - OUT camera, confirmed: the nearest matching occupant inside for at least
        min_presence_seconds -> EXIT (inside -1). No match or too soon -> NO_CHANGE.
        The count therefore never goes negative.

    resolve_known() removes at most the single nearest occupant (and at most the nearest
    candidate per camera) within the stricter resolve_known_distance when a face is later
    recognised as a registered student, so one person is not counted both ways.

    A confirmed occupant's stored encoding is only updated with a raw sighting that is itself
    within same_person_distance of it, so a distant face cannot drag the occupant's
    representation towards another person.

    Lifecycle:
      - Local-day change: occupants who entered on an earlier local day, recent exits and
        candidates are dropped, and entries_today restarts at 0 (checked lazily on each call).
        The day only moves forward: a late, out-of-order timestamp from an earlier day never
        resets state; such sightings/resolutions are ignored.
      - clear_camera(): drops one camera's candidates; confirmed occupants stay inside.
      - Process restart: all state is in memory only and starts empty (inside count 0).
        tracking_since records when this instance started counting.
      - Nothing here is persisted or logged; encodings of unregistered people are discarded
        on exit, day change, resolve_known() or restart.

    Timestamps are wall-clock epoch seconds.
    """

    def __init__(self,
                 same_person_distance: float = 0.6,
                 min_consecutive_frames: int = 5,
                 max_frame_gap: float = 2.0,
                 exit_cooldown: float = 60.0,
                 min_presence_seconds: float = 60.0,
                 resolve_known_distance: float = 0.45,
                 now: Optional[float] = None):
        """Thresholds (validated; ValueError if invalid):
          same_person_distance   > 0   L2 distance treated as the same person
          resolve_known_distance > 0   and <= same_person_distance (resolve_known)
          min_consecutive_frames >= 1  integer
          max_frame_gap          > 0   seconds
          exit_cooldown          >= 0  seconds (0 = no re-entry cooldown)
          min_presence_seconds   >= 0  seconds (0 = exit allowed immediately)
        """
        def finite(name, value):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number (got {value!r})")
            return float(value)

        if finite("same_person_distance", same_person_distance) <= 0:
            raise ValueError("same_person_distance must be > 0")
        if finite("resolve_known_distance", resolve_known_distance) <= 0:
            raise ValueError("resolve_known_distance must be > 0")
        if resolve_known_distance > same_person_distance:
            raise ValueError("resolve_known_distance must not be larger than same_person_distance")
        if isinstance(min_consecutive_frames, bool) or not isinstance(min_consecutive_frames, int) \
                or min_consecutive_frames < 1:
            raise ValueError(f"min_consecutive_frames must be an integer >= 1 (got {min_consecutive_frames!r})")
        if finite("max_frame_gap", max_frame_gap) <= 0:
            raise ValueError("max_frame_gap must be > 0")
        if finite("exit_cooldown", exit_cooldown) < 0:
            raise ValueError("exit_cooldown must be >= 0")
        if finite("min_presence_seconds", min_presence_seconds) < 0:
            raise ValueError("min_presence_seconds must be >= 0")

        self.same_person_distance = same_person_distance
        self.resolve_known_distance = resolve_known_distance
        self.min_consecutive_frames = min_consecutive_frames
        self.max_frame_gap = max_frame_gap
        self.exit_cooldown = exit_cooldown
        self.min_presence_seconds = min_presence_seconds

        self._lock = threading.RLock()
        self.tracking_since = time.time() if now is None else now
        self._candidates: Dict[Any, List[_UnknownCandidate]] = {}
        self._inside: List[_UnknownOccupant] = []
        self._recent_exits: List[Tuple[np.ndarray, float]] = []  # (encoding, exited_at)
        self._entries_today = 0
        self._day: Optional[Tuple[int, int, int]] = None
        self._next_id = 1

    @classmethod
    def from_settings(cls, settings, now: Optional[float] = None) -> "UnknownOccupancyTracker":
        """Build from app.config settings (thresholds shared with AttendanceEngine)"""
        return cls(
            same_person_distance=settings.UNKNOWN_SAME_PERSON_DISTANCE,
            min_consecutive_frames=settings.ATTENDANCE_MIN_CONSECUTIVE_FRAMES,
            max_frame_gap=settings.ATTENDANCE_MAX_FRAME_GAP_SECONDS,
            exit_cooldown=settings.ATTENDANCE_EXIT_COOLDOWN_SECONDS,
            min_presence_seconds=settings.ATTENDANCE_MIN_PRESENCE_SECONDS,
            resolve_known_distance=settings.UNKNOWN_RESOLVE_KNOWN_DISTANCE,
            now=now,
        )

    # --- helpers (caller holds the lock) ---------------------------------------------

    @staticmethod
    def _distance(a: np.ndarray, b: np.ndarray) -> float:
        return float(np.linalg.norm(np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)))

    def _nearest(self, items, encoding, get_encoding, threshold: Optional[float] = None):
        """Nearest item within threshold (default same_person_distance), or None"""
        limit = self.same_person_distance if threshold is None else threshold
        best, best_dist = None, None
        for item in items:
            dist = self._distance(get_encoding(item), encoding)
            if dist <= limit and (best_dist is None or dist < best_dist):
                best, best_dist = item, dist
        return best

    @staticmethod
    def _blend(mean: np.ndarray, samples: int, encoding: np.ndarray) -> Tuple[np.ndarray, int]:
        """Running mean of all accepted samples"""
        return (mean * samples + np.asarray(encoding, dtype=np.float64)) / (samples + 1), samples + 1

    def _is_earlier_day(self, now: float) -> bool:
        return self._day is not None and time.localtime(now)[:3] < self._day

    def _roll_day(self, now: float):
        """Drop previous-day state when the local calendar day moves forward. An earlier-day
        (out-of-order) timestamp never moves the day back or resets anything."""
        day = time.localtime(now)[:3]
        if self._day is None:
            self._day = day
            return
        if day > self._day:
            self._day = day
            self._inside = [o for o in self._inside if time.localtime(o.entered_at)[:3] == day]
            self._recent_exits = []
            self._candidates = {}
            self._entries_today = 0

    def _prune_recent_exits(self, now: float):
        self._recent_exits = [(enc, t) for enc, t in self._recent_exits if now - t < self.exit_cooldown]

    def _result(self, event: str, reasoning: str, occupant_id: Optional[int] = None) -> UnknownObservationResult:
        return UnknownObservationResult(event=event, inside_count=len(self._inside),
                                        occupant_id=occupant_id, reasoning=reasoning)

    # --- public API ---------------------------------------------------------------------

    def observe(self,
                kind: FaceObservationKind,
                encoding: np.ndarray,
                timestamp: float,
                camera_type: str,
                camera_key: Any,
                max_frame_gap: Optional[float] = None) -> UnknownObservationResult:
        """Record one face sighting from a camera (see FaceObservationKind for the contract).
        max_frame_gap optionally widens the configured gap for slow frames (see
        AttendanceEngine.process_recognition)."""
        gap_limit = max(self.max_frame_gap, max_frame_gap or 0.0)
        with self._lock:
            self._roll_day(timestamp)
            if self._is_earlier_day(timestamp):
                return self._result("IGNORED", "Late sighting from an earlier day")
            if kind != FaceObservationKind.UNKNOWN:
                return self._result("IGNORED", f"{kind.value} face: not an unregistered person")

            encoding = np.asarray(encoding, dtype=np.float64)

            # Candidates of this camera not seen within the frame gap are dropped
            candidates = [c for c in self._candidates.get(camera_key, [])
                          if timestamp - c.last_seen <= gap_limit]
            candidate = self._nearest(candidates, encoding, lambda c: c.encoding)
            if candidate is None:
                candidate = _UnknownCandidate(encoding=encoding.copy(), samples=0, consecutive=0, last_seen=timestamp)
                candidates.append(candidate)
            candidate.encoding, candidate.samples = self._blend(candidate.encoding, candidate.samples, encoding)
            candidate.consecutive += 1
            candidate.last_seen = timestamp
            self._candidates[camera_key] = candidates

            if candidate.consecutive < self.min_consecutive_frames:
                return self._result(
                    "CANDIDATE",
                    f"Need {self.min_consecutive_frames - candidate.consecutive} more consecutive sightings")

            self._prune_recent_exits(timestamp)
            occupant = self._nearest(self._inside, candidate.encoding, lambda o: o.encoding)

            if camera_type.upper() == "IN":
                if occupant is not None:
                    # Only a raw sighting that itself looks like this occupant may refine it
                    # (matching above used the candidate's mean, which can differ from the frame)
                    if self._distance(encoding, occupant.encoding) <= self.same_person_distance:
                        occupant.encoding, occupant.samples = self._blend(occupant.encoding, occupant.samples, encoding)
                    occupant.last_seen = timestamp
                    return self._result("REFRESHED", "Already counted inside", occupant.occupant_id)
                if self._nearest(self._recent_exits, candidate.encoding, lambda e: e[0]) is not None:
                    return self._result("NO_CHANGE", f"Exited less than {self.exit_cooldown}s ago")
                occupant = _UnknownOccupant(
                    occupant_id=self._next_id, encoding=candidate.encoding.copy(), samples=candidate.samples,
                    entered_at=timestamp, last_seen=timestamp, camera_key=camera_key)
                self._next_id += 1
                self._inside.append(occupant)
                self._entries_today += 1
                return self._result("ENTRY", "Unregistered person entered", occupant.occupant_id)

            # OUT camera
            if occupant is None:
                return self._result("NO_CHANGE", "No matching unregistered person inside")
            if timestamp - occupant.entered_at < self.min_presence_seconds:
                return self._result(
                    "NO_CHANGE",
                    f"Only {timestamp - occupant.entered_at:.1f}s inside (min {self.min_presence_seconds}s)",
                    occupant.occupant_id)
            self._inside.remove(occupant)
            self._recent_exits.append((occupant.encoding, timestamp))
            return self._result("EXIT", "Unregistered person exited", occupant.occupant_id)

    def resolve_known(self, encoding: np.ndarray, timestamp: float) -> int:
        """A face matching `encoding` was recognised as a registered student (e.g. their ENTRY
        was recorded): remove at most the single nearest unknown occupant, and at most the
        nearest candidate per camera, within resolve_known_distance. Never removes several
        occupants at once. Returns the number of occupants removed (0 or 1); entries_today is
        reduced by the same amount. Late calls stamped on an earlier day are ignored (0).

        resolve_known_distance is provisional: it lowers, but cannot eliminate, the risk of
        removing a different person who merely looks similar."""
        with self._lock:
            self._roll_day(timestamp)
            if self._is_earlier_day(timestamp):
                return 0
            encoding = np.asarray(encoding, dtype=np.float64)
            removed = 0
            occupant = self._nearest(self._inside, encoding, lambda o: o.encoding, self.resolve_known_distance)
            if occupant is not None:
                self._inside.remove(occupant)
                removed = 1
            self._entries_today = max(0, self._entries_today - removed)
            for key, candidates in self._candidates.items():
                candidate = self._nearest(candidates, encoding, lambda c: c.encoding, self.resolve_known_distance)
                if candidate is not None:
                    self._candidates[key] = [c for c in candidates if c is not candidate]
            return removed

    def clear_camera(self, camera_key: Any):
        """Forget one camera's candidates (camera stopped); confirmed occupants stay inside"""
        with self._lock:
            self._candidates.pop(camera_key, None)

    def inside_count(self, now: Optional[float] = None) -> int:
        with self._lock:
            self._roll_day(time.time() if now is None else now)
            return len(self._inside)

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Counts for status/dashboard use (no encodings)"""
        with self._lock:
            self._roll_day(time.time() if now is None else now)
            return {
                "unknown_inside_count": len(self._inside),
                "unknown_entries_today": self._entries_today,
                "unknown_tracking_since": self.tracking_since,
            }

    def candidate_count(self, camera_key: Any) -> int:
        with self._lock:
            return len(self._candidates.get(camera_key, []))


# One unregistered-occupancy tracker shared by every camera worker (in memory only)
_shared_unknown_tracker: Optional[UnknownOccupancyTracker] = None
_shared_unknown_tracker_lock = threading.Lock()


def get_shared_unknown_tracker(**kwargs) -> UnknownOccupancyTracker:
    """Return the process-wide UnknownOccupancyTracker, creating it with kwargs on first use"""
    global _shared_unknown_tracker
    with _shared_unknown_tracker_lock:
        if _shared_unknown_tracker is None:
            _shared_unknown_tracker = UnknownOccupancyTracker(**kwargs)
        return _shared_unknown_tracker


def get_shared_unknown_tracker_if_exists() -> Optional[UnknownOccupancyTracker]:
    """The shared tracker, or None if no camera has created it yet (does not create one)"""
    with _shared_unknown_tracker_lock:
        return _shared_unknown_tracker


def reset_shared_unknown_tracker():
    """Drop the shared tracker (tests)"""
    global _shared_unknown_tracker
    with _shared_unknown_tracker_lock:
        _shared_unknown_tracker = None


class LatestFrameReader:
    """Reads a live source continuously on its own thread and keeps only the newest frame.

    Recognition takes hundreds of milliseconds per frame on a CPU. Reading a live stream
    one frame per processing cycle would work through an ever-growing backlog of old,
    buffered frames (and stamp them with the current time); this reader always hands out
    the most recent frame and counts the ones skipped. The reader owns the capture and
    releases it on its own thread, so a capture is never released while a read is running.
    """

    def __init__(self, cap, name: str = "camera-reader"):
        self._cap = cap
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0          # frames read so far
        self._consumed = 0     # seq of the last frame handed out
        self.skipped = 0       # frames read but never processed (newer ones were available)
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True, name=name)
        self._thread.start()

    def _run(self):
        try:
            while True:
                with self._cond:
                    if self._stopped:
                        break
                ok, frame = self._cap.read()
                with self._cond:
                    if self._stopped:
                        break
                    if ok and frame is not None:
                        self._frame, self._seq = frame, self._seq + 1
                        self._cond.notify_all()
                if not ok:
                    time.sleep(0.05)  # do not spin on a dead stream
        except Exception as e:  # never let a decoder error kill the process silently
            logger.error(f"Camera reader stopped: {type(e).__name__}: {mask_source(e)}")
        finally:
            try:
                self._cap.release()
            except Exception:
                pass

    def next_frame(self, timeout: float) -> Optional[np.ndarray]:
        """The newest frame not handed out before, or None if none arrives within timeout"""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq == self._consumed and not self._stopped:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            if self._seq == self._consumed:
                return None
            self.skipped += self._seq - self._consumed - 1
            self._consumed = self._seq
            return self._frame

    def stop(self):
        """Ask the reader to stop; it releases the capture when its current read returns"""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()


class VideoProcessingService:
    """One camera's pipeline: capture -> person detection -> tracking -> face detection ->
    recognition (KNOWN / UNKNOWN / AMBIGUOUS) -> attendance engine (known students) and
    unknown-occupancy tracker (unregistered faces) -> annotated frame.

    Camera states (get_stats()['state']):
      stopped       - not started, or stopped by the operator
      running       - frames are being read
      disconnected  - the source stopped delivering frames
      reconnecting  - auto_reconnect is on and a reopen is scheduled / in progress
      ended         - a recorded video file reached its end
    feed_kind tells the UI what the frames are: 'live' (webcam / stream), 'recorded'
    (video file) or 'synthetic' (generated test frames, development only).
    """

    RECENT_OBSERVATIONS = 100
    OBSERVATION_THROTTLE_SECONDS = 2.0  # repeated sightings of one identity are logged at most this often

    def __init__(self,
                 video_source: Any = 0,  # 0 for webcam, or video file path
                 frame_width: int = 640,
                 frame_height: int = 480,
                 fps: int = 30,
                 camera_type: str = "IN",  # IN or OUT
                 camera_id: Optional[int] = None,
                 attendance_engine: Optional[AttendanceEngine] = None,  # pass the shared engine
                 face_detector: Optional[FaceDetectionService] = None,
                 allow_synthetic_source: bool = False,  # dev/test only
                 unknown_tracker: Optional["UnknownOccupancyTracker"] = None,  # pass the shared tracker
                 source_type: Optional[str] = None,  # webcam / video / rtsp (camera record)
                 person_detector: Optional[PersonDetectionService] = None,
                 auto_reconnect: bool = False,
                 reconnect_max_delay: float = 30.0,
                 stale_after_seconds: float = 5.0,
                 latest_frame_only: bool = False,  # live sources: always process the newest frame
                 open_timeout_seconds: float = 10.0,  # network streams only
                 read_timeout_seconds: float = 5.0,  # network streams only
                 zone: Any = None):  # the camera's classroom id: attendance state is kept per classroom
        self.zone = zone
        # Handle string numeric values for video_source (e.g., "0" for webcam)
        if isinstance(video_source, str) and video_source.isdigit():
            self.video_source = int(video_source)
        else:
            self.video_source = video_source
        self.source_type = (source_type or "").lower() or None
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.fps = fps
        self.camera_type = camera_type.upper()  # IN or OUT
        self.camera_id = camera_id
        # Key for per-camera state in the shared engine/tracker
        self.camera_key = camera_id if camera_id is not None else f"{self.camera_type}-{id(self)}"

        # Initialize services
        self.person_detector = person_detector or PersonDetectionService()
        self.face_detector = face_detector or FaceDetectionService()
        self.tracker = TrackingService()
        self.attendance_engine = attendance_engine or AttendanceEngine()
        # None = unregistered occupancy is not tracked by this service
        self.unknown_tracker = unknown_tracker

        # Video capture
        self.cap = None
        self.is_running = False
        self.state = "stopped"
        # Synthetic frames are only used when explicitly enabled (development/testing)
        self.allow_synthetic_source = allow_synthetic_source
        self.use_test_source = False
        self.last_error: Optional[str] = None  # Human-readable camera error (credentials masked)
        self.pipeline_error: Optional[str] = None  # Model/processing problem (camera itself may be fine)
        self.last_frame_time = 0
        self.frame_drop_count = 0
        self.consecutive_failures = 0
        self.consecutive_read_failures = 0
        self.max_consecutive_failures = 10  # Consider camera unhealthy after this many failures
        # Reconnection (live sources only, opt-in so tests never reopen a device)
        self.auto_reconnect = auto_reconnect
        self.reconnect_max_delay = reconnect_max_delay
        self.reconnect_attempts = 0
        self.total_reconnects = 0
        self._next_reconnect_at: Optional[float] = None
        self.stale_after_seconds = stale_after_seconds
        self.latest_frame_only = latest_frame_only
        self.open_timeout_seconds = open_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds
        self._reader: Optional[LatestFrameReader] = None
        self.read_wait_seconds = 1.0  # latest-frame mode: wait this long for a new frame
        self.started_at: Optional[float] = None
        self.last_frame_at: Optional[float] = None  # wall clock of the last frame read
        self.last_processing_ms: Optional[float] = None
        self.avg_processing_ms: Optional[float] = None  # exponential moving average
        self._prev_frame_timestamp: Optional[float] = None
        self.frame_interval_avg: Optional[float] = None  # seconds between processed frames (EMA)

        # Tracking information
        self.current_inside_count = 0  # Number of known people currently inside
        self.unknown_inside_count = 0  # Kept for compatibility; see unknown_tracker
        self.total_known_recognitions = {}  # student_id -> count of recognitions
        self.total_unknown_seen = 0  # Faces classified UNKNOWN by this camera (sightings, not people)
        self.total_ambiguous_seen = 0
        self.total_low_quality_faces = 0  # skipped as too small / blurred to identify
        # Diagnostic logging throttling
        self.last_diagnostic_log_time = 0  # Throttle diagnostic logs to once every 2 seconds
        # Frame storage for retrieval
        self.latest_annotated_frame = None  # Latest processed frame with annotations
        self.latest_frame_timestamp = 0     # Timestamp of latest frame

        # Frame processing stats
        self.frame_count = 0
        self.processed_attendance_events = []  # Store recent attendance decisions
        # Recent recognitions/events for the live view (no encodings, no images)
        self.recent_observations: collections.deque = collections.deque(maxlen=self.RECENT_OBSERVATIONS)
        self._last_observation_at: Dict[Any, float] = {}
        self.student_names: Dict[int, str] = {}

    @property
    def feed_kind(self) -> str:
        """'synthetic', 'recorded' (video file) or 'live' (webcam / network stream)"""
        if self.use_test_source:
            return "synthetic"
        if self.source_type == "video":
            return "recorded"
        if self.source_type in ("webcam", "rtsp") or isinstance(self.video_source, int):
            return "live"
        source = str(self.video_source)
        return "live" if "://" in source else "recorded"

    @property
    def masked_source(self) -> str:
        return mask_source(self.video_source)

    def get_latest_frame_jpeg(self):
        """Get the latest annotated frame as JPEG bytes"""
        if self.latest_annotated_frame is None:
            return None

        # Encode frame as JPEG
        _, buffer = cv2.imencode('.jpg', self.latest_annotated_frame)
        return buffer.tobytes()

    def _generate_test_frame(self) -> np.ndarray:
        """Generate a synthetic test frame for when no camera is available"""
        # Create a dark frame (like a classroom)
        frame = np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8)

        # Add some random noise to make it more realistic
        noise = np.random.randint(0, 30, (self.frame_height, self.frame_width, 3), dtype=np.uint8)
        frame = cv2.add(frame, noise)

        # Add a gradient to simulate lighting
        for i in range(self.frame_height):
            intensity = int(50 + (i / self.frame_height) * 30)  # Darker at top, lighter at bottom
            frame[i:i+1, :] = intensity

        # Occasionally add some shapes that could be detected as persons
        # This is just to test the pipeline - in reality these won't be recognized as known people
        if self.frame_count % 90 == 0:  # Every 3 seconds at 30fps
            # Add a rectangle that might be detected as a person
            x1, y1 = self.frame_width // 4, self.frame_height // 3
            x2, y2 = x1 + self.frame_width // 3, y1 + self.frame_height // 2
            cv2.rectangle(frame, (x1, y1), (x2, y2), (100, 150, 200), -1)  # Filled rectangle
            # Add some internal details
            cv2.rectangle(frame, (x1+10, y1+10), (x2-10, y2-10), (50, 100, 150), -1)

        return frame

    def _open_capture(self):
        """Open the source and apply the requested size/fps. Returns the capture or None.
        Network streams get bounded open/read timeouts, so an unreachable or stalled camera
        is reported within seconds instead of blocking for FFmpeg's default ~30 s."""
        source = self.video_source
        if isinstance(source, str) and "://" in source:
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self.open_timeout_seconds * 1000),
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.read_timeout_seconds * 1000)])
        else:
            cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        return cap

    def warm_up(self) -> float:
        """Load the models on a blank image before the first camera frame. First use of the
        person detector, the face library and the tracker takes several seconds; without this
        the first real frame stalls and the timing statistics start from that outlier.
        Touches no counters, decisions or occupancy state. Returns the seconds taken."""
        started = time.perf_counter()
        blank = np.zeros((self.frame_height, self.frame_width, 3), np.uint8)
        try:
            self.person_detector.detect_persons(blank)
            self.face_detector.detect_faces(blank, [(0, 0, self.frame_width, self.frame_height)])
            self.tracker.update_tracks(blank, [])
        except Exception as e:  # the first real frame reports any lasting problem
            logger.warning(f"Model warm-up for camera {self.camera_key} failed: {type(e).__name__}: {e}")
        seconds = time.perf_counter() - started
        logger.info(f"Models ready for camera {self.camera_key} in {seconds:.1f}s")
        return seconds

    def _attach_reader(self):
        """Live sources in latest-frame mode read on their own thread (see LatestFrameReader)"""
        if self.latest_frame_only and self.cap is not None and self.feed_kind == "live":
            self._reader = LatestFrameReader(self.cap, name=f"camera-reader-{self.camera_key}")

    def _release_capture(self):
        if self._reader is not None:
            self._reader.stop()  # the reader thread releases the capture itself
            self._reader = None
        elif self.cap is not None:
            self.cap.release()
        self.cap = None

    def fit_to_frame_size(self, frame: np.ndarray) -> np.ndarray:
        """Scale to fit within frame_width x frame_height, keeping the aspect ratio. Stretching
        a 16:9 CCTV image into 4:3 would squash every face and degrade recognition."""
        h, w = frame.shape[:2]
        if (w, h) == (self.frame_width, self.frame_height):
            return frame
        scale = min(self.frame_width / w, self.frame_height / h)
        size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
        if size == (w, h):
            return frame
        return cv2.resize(frame, size, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)

    def start(self):
        """Start video capture. Raises CameraOpenError if the source cannot be opened
        (unless synthetic frames were explicitly allowed for development/testing)."""
        try:
            self.cap = self._open_capture()
            if self.cap is None:
                message = (f"Could not open camera source '{self.masked_source}'. Check that the device is "
                           f"connected and not in use by another application, or that the file path / stream "
                           f"URL is correct.")
                if not self.allow_synthetic_source:
                    self.last_error = message
                    self.state = "disconnected"
                    logger.error(message)
                    raise CameraOpenError(message)
                logger.warning(f"{message} Synthetic test frames are ENABLED (development only).")
                self.use_test_source = True
            else:
                self.use_test_source = False
                self._attach_reader()
                logger.info(f"Started video processing from source: {self.masked_source}")

            self.is_running = True
            self.state = "running"
            self.started_at = time.time()
            self.last_frame_time = time.time()
            self.consecutive_failures = 0
            self.consecutive_read_failures = 0

        except CameraOpenError:
            self.is_running = False
            raise
        except Exception as e:
            logger.error(f"Failed to start video processing: {mask_source(e)}")
            self.is_running = False
            raise

    def stop(self):
        """Stop video capture. Confirmed occupants stay in the shared tracker; only this
        camera's unconfirmed candidates are dropped."""
        self.is_running = False
        self.state = "stopped"
        self._release_capture()
        if self.unknown_tracker is not None:
            self.unknown_tracker.clear_camera(self.camera_key)
        logger.info("Stopped video processing")

    # --- frame acquisition ---------------------------------------------------------------

    def _schedule_reconnect(self, now: float):
        delay = min(self.reconnect_max_delay, 2.0 ** min(self.reconnect_attempts, 10))
        self._next_reconnect_at = now + delay
        self.state = "reconnecting"

    def _try_reconnect(self, now: float):
        """Reopen a dropped live source once the backoff delay has passed"""
        if self._next_reconnect_at is None or now < self._next_reconnect_at:
            return
        self.reconnect_attempts += 1
        cap = self._open_capture()
        if cap is None:
            self.last_error = (f"Camera source '{self.masked_source}' is disconnected; reconnect attempt "
                               f"{self.reconnect_attempts} failed")
            logger.warning(self.last_error)
            self._schedule_reconnect(now)
            return
        self.cap = cap
        self._attach_reader()
        self.total_reconnects += 1
        self.reconnect_attempts = 0
        self._next_reconnect_at = None
        self.consecutive_read_failures = 0
        self.consecutive_failures = 0
        self.last_error = None
        self.state = "running"
        logger.info(f"Reconnected to camera source '{self.masked_source}'")

    def _read_frame(self) -> Optional[np.ndarray]:
        """Next frame, or None (failure / disconnected / end of file). Never fabricates a
        frame for a real source."""
        if self.use_test_source:
            # Generate synthetic test frame (only when explicitly enabled)
            return self._generate_test_frame()

        now = time.time()
        if self.cap is None:
            if self.auto_reconnect and self.state == "reconnecting":
                self._try_reconnect(now)
            return None

        if self._reader is not None:
            frame = self._reader.next_frame(self.read_wait_seconds)
            ret = frame is not None
            now = time.time()
        else:
            ret, frame = self.cap.read()
        if ret and frame is not None:
            if self.consecutive_read_failures:
                self.consecutive_read_failures = 0
                self.last_error = None
            self.state = "running"
            self.last_frame_at = now
            return frame

        # Do not run detection on fabricated frames; report the camera problem instead
        self.consecutive_read_failures += 1
        self.consecutive_failures = self.consecutive_read_failures
        if self.feed_kind == "recorded" and self.frame_count > 0:
            # A video file that has delivered frames and now returns nothing has ended
            self.state = "ended"
            self.last_error = None
            if self.consecutive_read_failures == 1:
                logger.info(f"End of video file '{self.masked_source}' after {self.frame_count} frames")
            return None
        if self.consecutive_read_failures >= self.max_consecutive_failures:
            self.last_error = (f"Camera source '{self.masked_source}' stopped delivering frames "
                               f"({self.consecutive_read_failures} consecutive read failures)")
            if self.consecutive_read_failures == self.max_consecutive_failures:
                logger.error(self.last_error)
                self.state = "disconnected"
                if self.auto_reconnect and self.feed_kind == "live":
                    self._release_capture()
                    self._schedule_reconnect(now)
        else:
            logger.debug("Failed to read frame")
        return None

    def load_known_faces_from_db(self, db_session):
        """Load known face embeddings from database"""
        try:
            from app import crud, face_models

            # Get all active face embeddings with student info
            embeddings = crud.get_face_embeddings(db_session, active_only=True)

            face_data = []
            names = {}
            skipped_other_model = 0
            for embedding in embeddings:
                if embedding.student is not None and (not embedding.student.is_active
                                                      or embedding.student.deleted_at is not None):
                    continue  # Inactive / deleted students are not recognised
                if embedding.student is not None:
                    names[embedding.student_id] = embedding.student.name
                # Only encodings made by this worker's model are comparable (app/face_models.py)
                model, face_encoding = face_models.decode(embedding.embedding_data)
                if model is None:
                    logger.warning(f"Skipping face embedding {embedding.id}: unrecognised format "
                                   f"({len(embedding.embedding_data or b'')} bytes)")
                    continue
                if model != self.face_detector.recognition_model:
                    skipped_other_model += 1
                    continue
                face_data.append((embedding.student_id, face_encoding))

            # Atomic swap; a worker mid-frame keeps using its snapshot
            self.face_detector.load_known_faces(face_data)
            self.student_names = names
            self.faces_needing_reenrollment = skipped_other_model
            logger.info(f"Loaded {len(face_data)} known faces ({self.face_detector.recognition_model}); "
                        f"{skipped_other_model} enrolled with another model are not used")
            return True

        except Exception as e:
            logger.error(f"Failed to load known faces from database: {type(e).__name__}: {e}")
            # Keep the previously loaded faces (empty on first load -> everyone is unknown)
            return False

    def _publish_frame(self, frame: np.ndarray, timestamp: float) -> np.ndarray:
        """Store the frame for /video/{id}/frame (reference swap, safe across threads)"""
        self.latest_annotated_frame = frame.copy()
        self.latest_frame_timestamp = timestamp
        return frame

    def process_frame(self,
                      db_session,
                      record_event: Optional[Callable[[AttendanceDecision], None]] = None
                      ) -> Tuple[Optional[np.ndarray], List[AttendanceDecision]]:
        """Process a single frame and return annotated frame with attendance decisions.

        record_event, if given, persists each ENTRY/EXIT decision; it runs under the
        shared engine lock and a failure rolls the decision back (see AttendanceEngine).
        Returns (None, []) when no frame could be read from the camera.
        """
        if not self.is_running:
            raise RuntimeError("Video processing not started")

        frame = self._read_frame()
        if frame is None:
            return None, []

        # Scale to the configured size without distorting faces
        frame = self.fit_to_frame_size(frame)

        self.frame_count += 1
        timestamp = time.time()  # Wall-clock time so cooldowns are in real seconds
        if self._prev_frame_timestamp is not None:
            interval = timestamp - self._prev_frame_timestamp
            self.frame_interval_avg = (interval if self.frame_interval_avg is None
                                       else 0.8 * self.frame_interval_avg + 0.2 * interval)
        self._prev_frame_timestamp = timestamp
        started = time.perf_counter()
        try:
            return self._process_frame(frame, timestamp, record_event)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self.last_processing_ms = elapsed_ms
            self.avg_processing_ms = (elapsed_ms if self.avg_processing_ms is None
                                      else 0.9 * self.avg_processing_ms + 0.1 * elapsed_ms)

    def _record_observation(self, key: Any, timestamp: float, force: bool, **details):
        """Add an entry to the live-view log. Repeated sightings of the same identity are
        throttled; events (ENTRY/EXIT) are always kept."""
        last = self._last_observation_at.get(key)
        if not force and last is not None and timestamp - last < self.OBSERVATION_THROTTLE_SECONDS:
            return
        self._last_observation_at[key] = timestamp
        if len(self._last_observation_at) > 1000:  # bounded memory
            self._last_observation_at.clear()
        self.recent_observations.append({"timestamp": timestamp, "camera_id": self.camera_id,
                                         "camera_type": self.camera_type, **details})

    MAX_ADAPTIVE_GAP_SECONDS = 15.0

    def adaptive_frame_gap(self) -> Optional[float]:
        """Gap allowed between two sightings of the same face on this camera. When frames
        are processed slowly (many faces on a CPU without AVX can take seconds per frame),
        the configured gap would break every run of consecutive sightings, so it is
        widened to 2.5x the measured interval between frames (capped)."""
        if self.frame_interval_avg is None:
            return None
        return min(self.MAX_ADAPTIVE_GAP_SECONDS, 2.5 * self.frame_interval_avg)

    def _handle_face(self, index: int, kind: "FaceObservationKind", student_id: Optional[int],
                     confidence: float, encoding: np.ndarray, timestamp: float,
                     record_event) -> Optional[AttendanceDecision]:
        """Route one classified face: KNOWN -> attendance engine, UNKNOWN/AMBIGUOUS ->
        unknown-occupancy tracker (which ignores AMBIGUOUS). Returns the engine decision."""
        if kind == FaceObservationKind.KNOWN and student_id is not None:
            # Make attendance decision (shared engine; persisted atomically if recorder given)
            decision = self.attendance_engine.process_recognition(
                student_id, confidence, timestamp, self.camera_type,
                camera_id=self.camera_id, persist=record_event, max_frame_gap=self.adaptive_frame_gap(),
                zone=self.zone)
            accepted = confidence >= self.attendance_engine.confidence_threshold
            if self.unknown_tracker is not None and accepted:
                # A confidently recognised student may earlier have been counted as an
                # unregistered visitor (e.g. face first seen badly): remove at most the
                # single nearest unknown occupant within the strict resolve distance.
                self.unknown_tracker.resolve_known(encoding, timestamp)
            self._record_observation(
                ("known", student_id), timestamp, force=decision.should_record,
                kind="known", student_id=student_id, student_name=self.student_names.get(student_id),
                confidence=round(confidence, 1), decision=decision.event_type,
                recorded=decision.should_record, reasoning=decision.reasoning)
            return decision

        if kind == FaceObservationKind.AMBIGUOUS:
            self.total_ambiguous_seen += 1
        else:
            self.total_unknown_seen += 1
        result = None
        if self.unknown_tracker is not None:
            result = self.unknown_tracker.observe(kind, encoding, timestamp, self.camera_type, self.camera_key,
                                                  max_frame_gap=self.adaptive_frame_gap())
        event = result.event if result is not None else None
        self._record_observation(
            (kind.value, result.occupant_id if result is not None else None), timestamp,
            force=event in ("ENTRY", "EXIT"),
            kind=kind.value, student_id=None, student_name=None, confidence=None,
            decision=event or "NOT_TRACKED", recorded=event in ("ENTRY", "EXIT"),
            reasoning=result.reasoning if result is not None else "Unknown occupancy tracking disabled",
            unknown_occupant_id=result.occupant_id if result is not None else None)
        # Same shape as before for callers that inspect processed decisions
        return AttendanceDecision(student_id=None, confidence=0.0, event_type="UNKNOWN", timestamp=timestamp,
                                  should_record=False, reasoning=f"{kind.value} face", camera_id=self.camera_id)

    def _process_frame(self, frame: np.ndarray, timestamp: float,
                       record_event) -> Tuple[Optional[np.ndarray], List[AttendanceDecision]]:

        # Health monitoring: check for frame drops and capture failures
        current_time = time.time()
        if self.last_frame_time > 0:
            expected_interval = 1.0 / self.fps
            actual_interval = current_time - self.last_frame_time
            if actual_interval > expected_interval * 2:  # More than 2x expected interval
                # Processing slower than the source frame rate is normal on CPU; counted
                # and exposed as frame_drop_count instead of logging every frame
                self.frame_drop_count += 1
                logger.debug(f"Frame drop detected: {actual_interval:.3f}s vs expected {expected_interval:.3f}s")

        self.last_frame_time = current_time

        attendance_decisions = []

        try:
            # Step 1: Person Detection
            person_result = self.person_detector.detect_persons(frame)
            if person_result.status == DetectionStatus.PROCESSING_ERROR:
                self.pipeline_error = (self.person_detector.load_error
                                       if hasattr(self.person_detector, "load_error") and self.person_detector.load_error
                                       else "Person detection failed")
            else:
                self.pipeline_error = None

            # Step 2: Tracking runs on every frame (also with no detections, so tracks age
            # out during short occlusions), independent of whether faces are visible.
            person_detections_for_tracking = []
            for i, box in enumerate(person_result.boxes):
                x1, y1, x2, y2 = box
                conf = person_result.confidences[i] if i < len(person_result.confidences) else 0.8
                person_detections_for_tracking.append((x1, y1, x2, y2, conf))
            tracking_result = self.tracker.update_tracks(frame, person_detections_for_tracking)

            if person_result.status != DetectionStatus.SUCCESS or len(person_result.boxes) == 0:
                logger.debug(f"ATTEND_DIAG_EARLY_RETURN: Person detection - status:{person_result.status}, boxes:{len(person_result.boxes)}")
                # No persons detected, return original frame
                self.consecutive_failures = 0  # Reset failure count on success (no persons is OK)
                return self._publish_frame(frame, timestamp), attendance_decisions

            # Step 3: Face Detection within person regions
            face_result = self.face_detector.detect_faces(frame, person_result.boxes)
            self.total_low_quality_faces += getattr(face_result, "low_quality_count", 0)

            if face_result.status != DetectionStatus.SUCCESS or len(face_result.face_encodings) == 0:
                logger.debug(f"ATTEND_DIAG_EARLY_RETURN: Face detection - status:{face_result.status}, encodings:{len(face_result.face_encodings)}")
                # No faces detected (turned away, occluded, too small), but we have persons
                self.consecutive_failures = 0  # Reset failure count on success
                annotated_frame = self._draw_comprehensive_results(frame, person_result.boxes, [], [], [],
                                                                   tracking_result.tracks)
                return self._publish_frame(annotated_frame, timestamp), attendance_decisions

            # Step 3: Face Recognition
            recognition_result = self.face_detector.recognize_faces(face_result.face_encodings)

            if recognition_result.status == DetectionStatus.PROCESSING_ERROR:
                # Diagnostic logging before early return
                logger.info(f"ATTEND_DIAG_EARLY_RETURN: Recognition processing error - status:{recognition_result.status}")
                logger.error("Face recognition processing error")
                self.consecutive_failures += 1
                annotated_frame = self._draw_detection_results(frame, person_result.boxes, face_result.face_locations, [])
                return self._publish_frame(annotated_frame, timestamp), attendance_decisions

            # Reset failure count on successful processing up to this point
            self.consecutive_failures = 0

            # Step 5: Route each classified face (known -> attendance, unknown -> occupancy)
            face_locations = face_result.face_locations
            kinds = recognition_result.kinds

            for i, (student_id, confidence) in enumerate(zip(
                recognition_result.student_ids,
                recognition_result.confidences
            )):
                if i < len(face_locations) and i < len(face_result.face_encodings):
                    kind = kinds[i] if i < len(kinds) else (
                        FaceObservationKind.KNOWN if student_id is not None else FaceObservationKind.UNKNOWN)
                    decision = self._handle_face(i, kind, student_id, confidence, face_result.face_encodings[i],
                                                 timestamp, record_event)

                    if decision.should_record:
                        attendance_decisions.append(decision)

                    # Store recent decisions for debugging/stats
                    if len(self.processed_attendance_events) >= 50:
                        self.processed_attendance_events.pop(0)
                    self.processed_attendance_events.append({
                        'timestamp': timestamp,
                        'student_id': decision.student_id,
                        'event_type': decision.event_type,
                        'confidence': decision.confidence,
                        'reasoning': decision.reasoning
                    })

                    # Update recognition counts based on attendance decision
                    # (current_inside_count comes from the shared engine, see get_stats)
                    if decision.should_record and decision.student_id is not None:
                        self.total_known_recognitions[decision.student_id] = \
                            self.total_known_recognitions.get(decision.student_id, 0) + 1

            # Step 6: Annotate frame with results
            annotated_frame = self._draw_comprehensive_results(
                frame,
                person_result.boxes,
                face_locations,
                recognition_result.student_ids,
                recognition_result.confidences,
                tracking_result.tracks,
                kinds=kinds
            )

            # Throttled diagnostic logging (at most once every 2 seconds)
            current_time = time.time()
            if current_time - self.last_diagnostic_log_time >= 2.0:
                self.last_diagnostic_log_time = current_time

                # Frame analysis summary
                person_count = len(person_result.boxes)
                face_count = len(face_result.face_locations)
                recognition_count = len(recognition_result.student_ids)

                # Person detection details
                person_details = []
                for i, box in enumerate(person_result.boxes):
                    conf = person_result.confidences[i] if i < len(person_result.confidences) else 0.0
                    person_details.append(f"box{i}:{box} conf:{conf:.2f}")

                # Face detection and recognition details
                face_details = []
                for i in range(min(face_count, recognition_count)):
                    if i < len(face_result.face_locations) and i < len(recognition_result.student_ids):
                        loc = face_result.face_locations[i]
                        student_id = recognition_result.student_ids[i]
                        conf = recognition_result.confidences[i] if i < len(recognition_result.confidences) else 0.0

                        if student_id is not None:
                            face_details.append(f"KNOWN_ID:{student_id} conf:{conf:.1f}% loc:{loc}")
                        else:
                            face_details.append(f"UNKNOWN conf:{conf:.1f}% loc:{loc}")

                # Attendance decision details
                decision_details = []
                for decision in attendance_decisions:
                    decision_details.append(f"{decision.event_type} rec:{decision.should_record} conf:{decision.confidence:.1f}% reasoning:'{decision.reasoning}'")

                # Tracking info
                tracked_boxes = []
                for i, (x1, y1, x2, y2, conf) in enumerate(person_detections_for_tracking):
                    tracked_boxes.append(f"TRACK_DET{i}:{int(x1)},{int(y1)},{int(x2)},{int(y2)} conf:{conf:.2f}")

                confirmed_tracks_count = len([t for t in tracking_result.tracks if t is not None]) if hasattr(tracking_result, 'tracks') and tracking_result.tracks else 0

                # Find consecutive recognition counter for first recognized student (if any)
                consec_counter_info = "NO_RECOGNIZED_FACES"
                if recognition_result.student_ids:
                    # Look for first recognized student (non-None ID)
                    for i, student_id in enumerate(recognition_result.student_ids):
                        if student_id is not None:
                            # Get the student state to show consecutive counter
                            try:
                                camera_key = self.camera_id if self.camera_id is not None else self.camera_type
                                consec_counter = self.attendance_engine.get_consecutive(student_id, camera_key,
                                                                                        zone=self.zone)
                                consec_counter_info = f"ID:{student_id} consec:{consec_counter}/{self.attendance_engine.min_consecutive_frames}"
                                break
                            except Exception:
                                consec_counter_info = f"ID:{student_id} consec:ERROR"
                                break

                # Frame type classification
                if person_count == 0:
                    frame_type = "NO_PERSON"
                elif face_count == 0:
                    frame_type = "PERSON_NO_FACE"
                elif recognition_count == 0:
                    frame_type = "FACE_NO_RECOGNITION"
                elif any(sid is not None for sid in recognition_result.student_ids):
                    frame_type = "RECOGNIZED_FACE"
                else:
                    frame_type = "UNKNOWN_FACES_ONLY"

                logger.debug(
                    f"ATTEND_DIAG[timestamp:{timestamp:.1f}fps:{self.fps}] "
                    f"FRAME_TYPE:{frame_type} "
                    f"PERSONS:{person_count}[{'|'.join(person_details) if person_details else 'none'}] "
                    f"FACES:{face_count}[{'|'.join(face_details) if face_details else 'none'}] "
                    f"RECOGNITION:{recognition_count} "
                    f"DECISIONS:[{'|'.join(decision_details) if decision_details else 'none'}] "
                    f"TRACK_DETECTIONS:{len(person_detections_for_tracking)}[{'|'.join(tracked_boxes) if tracked_boxes else 'none'}] "
                    f"CONFIRMED_TRACKS:{confirmed_tracks_count} "
                    f"{consec_counter_info}"
                )

            # Store the latest annotated frame for retrieval
            return self._publish_frame(annotated_frame, timestamp), attendance_decisions

        except Exception as e:
            logger.error(f"Error processing frame: {mask_source(e)}")
            self.pipeline_error = f"Frame processing failed: {type(e).__name__}"
            self.consecutive_failures += 1
            # Check if camera is unhealthy
            if self.consecutive_failures >= self.max_consecutive_failures:
                logger.error(f"Camera appears unhealthy: {self.consecutive_failures} consecutive failures")
            # Return original frame on error
            return self._publish_frame(frame, timestamp), attendance_decisions

    def _draw_person_boxes(self, frame: np.ndarray, boxes: List[Tuple[int, int, int, int]]) -> np.ndarray:
        """Draw person bounding boxes on frame"""
        annotated = frame.copy()
        for box in boxes:
            x1, y1, x2, y2 = box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 0, 0), 2)  # Blue for persons
            cv2.putText(annotated, "Person", (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
        return annotated

    def _draw_detection_results(self,
                               frame: np.ndarray,
                               person_boxes: List[Tuple[int, int, int, int]],
                               face_locations: List[Tuple[int, int, int, int]],
                               face_names: List[str]) -> np.ndarray:
        """Draw detection results on frame"""
        annotated = frame.copy()

        # Draw person boxes
        for box in person_boxes:
            x1, y1, x2, y2 = box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 0, 0), 2)  # Blue
            cv2.putText(annotated, "Person", (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)

        # Draw face boxes
        for i, (top, right, bottom, left) in enumerate(face_locations):
            color = (0, 255, 0) if i < len(face_names) and face_names[i] != "Unknown" else (0, 0, 255)
            cv2.rectangle(annotated, (left, top), (right, bottom), color, 2)

            label = face_names[i] if i < len(face_names) else "Face"
            cv2.putText(annotated, label, (left, bottom + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        return annotated

    def _draw_comprehensive_results(self,
                                   frame: np.ndarray,
                                   person_boxes: List[Tuple[int, int, int, int]],
                                   face_locations: List[Tuple[int, int, int, int]],
                                   student_ids: List[Optional[int]],
                                   confidences: List[float],
                                   tracks: List[Dict[str, Any]],
                                   kinds: Optional[List["FaceObservationKind"]] = None) -> np.ndarray:
        """Draw comprehensive results including tracking"""
        annotated = frame.copy()
        self.last_track_count = len(tracks)
        kinds = kinds or []

        # Draw tracking results first (more stable)
        for track in tracks:
            track_id = track['track_id']
            x1, y1, x2, y2 = track['bbox']
            confidence = track['confidence']

            # Green for tracked objects
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(annotated, f"ID:{track_id} {confidence:.2f}",
                       (x1, y1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # Draw person detections (blue)
        for box in person_boxes:
            x1, y1, x2, y2 = box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 0, 0), 1)  # Thinner blue line

        # Draw face recognition results
        for i, (top, right, bottom, left) in enumerate(face_locations):
            if i < len(student_ids) and i < len(confidences):
                student_id = student_ids[i]
                confidence = confidences[i]

                kind = kinds[i] if i < len(kinds) else None
                name = self.student_names.get(student_id) if student_id is not None else None
                identity = name or f"ID:{student_id}"
                if student_id is not None and confidence >= self.attendance_engine.confidence_threshold:
                    # Known person with sufficient confidence - Green
                    color = (0, 255, 0)
                    label = f"{identity} {confidence:.0f}"
                elif student_id is not None:
                    # Known person but low match score - Yellow (not used for attendance)
                    color = (0, 255, 255)
                    label = f"{identity} {confidence:.0f} (low)"
                elif kind == FaceObservationKind.AMBIGUOUS:
                    # Close to more than one enrolled student - Orange, never counted as unregistered
                    color = (0, 165, 255)
                    label = "Ambiguous"
                else:
                    # Unregistered person - Red
                    color = (0, 0, 255)
                    label = "Unregistered"

                cv2.rectangle(annotated, (left, top), (right, bottom), color, 2)
                cv2.putText(annotated, label, (left, bottom + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        return annotated

    def get_recent_observations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Newest first. Identity details only - no encodings or images."""
        return list(self.recent_observations)[-limit:][::-1]

    def get_stats(self) -> Dict[str, Any]:
        """Get processing statistics"""
        now = time.time()
        frame_age = (now - self.last_frame_at) if self.last_frame_at else None
        if self.use_test_source and self.latest_frame_timestamp:
            frame_age = now - self.latest_frame_timestamp
        # Before the first frame, allow time for model loading (first inference) before
        # calling the feed stale
        startup_grace = max(self.stale_after_seconds, 30.0)
        stale = self.is_running and self.state == "running" and (
            frame_age is None and self.started_at is not None and now - self.started_at > startup_grace
            or frame_age is not None and frame_age > self.stale_after_seconds)
        unknown = self.unknown_tracker.snapshot(now) if self.unknown_tracker is not None else None

        return {
            'frames_processed': self.frame_count,
            'is_running': self.is_running,
            'state': self.state,
            'feed_kind': self.feed_kind,
            # Recorded files: just the file name (the server's folder layout is not shown)
            'source': (os.path.basename(str(self.video_source)) if self.feed_kind == "recorded"
                       else self.masked_source),
            'source_type': self.source_type,
            'camera_type': self.camera_type,
            'started_at': self.started_at,
            'last_frame_at': self.last_frame_at or (self.latest_frame_timestamp or None),
            'frame_age_seconds': round(frame_age, 2) if frame_age is not None else None,
            'stale': bool(stale),
            'last_processing_ms': round(self.last_processing_ms, 1) if self.last_processing_ms is not None else None,
            'avg_processing_ms': round(self.avg_processing_ms, 1) if self.avg_processing_ms is not None else None,
            # Latest-frame mode: live frames skipped because processing is slower than the feed
            'latest_frame_only': self._reader is not None,
            'frames_skipped': self._reader.skipped if self._reader is not None else 0,
            'recent_attendance_count': len(self.processed_attendance_events),
            'known_faces_count': len(self.face_detector.known_face_encodings),
            'active_tracks': getattr(self, 'last_track_count', 0),
            # Shared across cameras: students with an active entry today
            'current_inside_count': self.attendance_engine.inside_count(),
            # Shared across cameras: estimated unregistered people inside (None = not tracked)
            'unknown_inside_count': unknown["unknown_inside_count"] if unknown else self.unknown_inside_count,
            'unknown_tracking_since': unknown["unknown_tracking_since"] if unknown else None,
            'total_known_recognitions': len(self.total_known_recognitions),
            'total_unknown_seen': self.total_unknown_seen,
            'total_ambiguous_seen': self.total_ambiguous_seen,
            'total_low_quality_faces': self.total_low_quality_faces,
            'frame_drop_count': self.frame_drop_count,
            'consecutive_failures': self.consecutive_failures,
            'reconnect_attempts': self.reconnect_attempts,
            'total_reconnects': self.total_reconnects,
            'camera_healthy': (self.consecutive_failures < self.max_consecutive_failures and self.last_error is None
                               and self.state not in ("disconnected", "reconnecting")),
            'last_error': self.last_error,
            'pipeline_error': self.pipeline_error,
            'synthetic_source': self.use_test_source
        }