"""
Face enrollment router for AttendAI API.
Handles face image upload and encoding generation for student enrollment.
"""
import cv2
import numpy as np
import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status, File, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app import models, crud, schemas
from app.permissions import audit, require_operator, require_viewer
from app import face_models
from app.services import notify_known_faces_changed

logger = logging.getLogger(__name__)

router = APIRouter()

# Configuration
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/jpg"}
MAX_IMAGE_PIXELS = 40_000_000  # refuse decompression bombs

# Enrollment image quality gates. Heuristics chosen to reject obviously unusable photos,
# not calibrated measures of recognition quality.
MIN_FACE_PIXELS = 64          # shorter side of the detected face box
MIN_SHARPNESS = 30.0          # variance of the Laplacian over the face (lower = blurrier)
MIN_BRIGHTNESS, MAX_BRIGHTNESS = 50.0, 215.0  # mean grey level over the face


class EnrollmentResult(schemas.FaceEmbeddingResponse):
    quality: Dict[str, float] = {}
    warnings: List[str] = []


_SFACE = None


def _sface() -> face_models.SFaceModel:
    """One SFace instance for enrollment requests (its calls are serialised by its lock)"""
    global _SFACE
    if _SFACE is None:
        _SFACE = face_models.SFaceModel()
    return _SFACE


def assess_face_quality(rgb_image: np.ndarray, location) -> Dict[str, float]:
    top, right, bottom, left = location
    h, w = rgb_image.shape[:2]
    crop = rgb_image[max(0, top):min(h, bottom), max(0, left):min(w, right)]
    if crop.size == 0:
        return {"face_size": 0.0, "sharpness": 0.0, "brightness": 0.0}
    grey = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    return {
        "face_size": float(min(bottom - top, right - left)),
        "sharpness": round(float(cv2.Laplacian(grey, cv2.CV_64F).var()), 1),
        "brightness": round(float(grey.mean()), 1),
    }


def quality_problems(quality: Dict[str, float]) -> List[str]:
    problems = []
    if quality["face_size"] < MIN_FACE_PIXELS:
        problems.append(f"The face is too small ({quality['face_size']:.0f} px; at least {MIN_FACE_PIXELS} px "
                        f"needed). Move closer or crop the photo around the face.")
    if quality["sharpness"] < MIN_SHARPNESS:
        problems.append("The face is blurred. Hold the camera still and make sure it is in focus.")
    if quality["brightness"] < MIN_BRIGHTNESS:
        problems.append("The face is too dark. Add light in front of the person.")
    elif quality["brightness"] > MAX_BRIGHTNESS:
        problems.append("The face is over-exposed. Avoid direct light or flash.")
    return problems


def identity_conflicts(db: Session, student: models.Student, encoding: np.ndarray,
                       model: str = face_models.DLIB) -> List[str]:
    """Warnings when the new face looks like somebody else, or unlike this person's
    existing enrolled faces - typical signs of selecting the wrong record. Only faces
    enrolled with the same recognition model are comparable."""
    warnings = []
    limits = face_models.thresholds(model)
    tolerance = limits["match_tolerance"]  # own photos: is this still the same person?
    lookalike = limits["enroll_lookalike"]  # other students: too close to someone else?
    rows = db.query(models.FaceEmbedding).filter(models.FaceEmbedding.is_active == True).all()  # noqa: E712
    own, others = [], []
    for row in rows:
        row_model, vec = face_models.decode(row.embedding_data)
        if row_model != model:
            continue
        (own if row.student_id == student.id else others).append((row.student_id, vec))
    if others:
        distances = face_models.distances(model, [v for _, v in others], encoding)
        best = int(np.argmin(distances))
        if distances[best] <= lookalike:
            other = db.query(models.Student).filter(models.Student.id == others[best][0]).first()
            label = f"{other.name} ({other.student_id})" if other else f"record {others[best][0]}"
            warnings.append(f"This face is similar to {label} (distance {distances[best]:.2f}). "
                            f"Check that the right person is selected.")
    if own:
        distances = face_models.distances(model, [v for _, v in own], encoding)
        if float(np.min(distances)) > tolerance:
            warnings.append(f"This face does not match the {len(own)} photo(s) already enrolled for "
                            f"{student.name} (closest distance {float(np.min(distances)):.2f}).")
    return warnings


class FaceRecord(BaseModel):
    id: int
    is_active: bool
    created_at: schemas.UTCDateTime


@router.get("/{student_id}", response_model=List[FaceRecord])
async def list_faces(student_id: int, db: Session = Depends(get_db), current_user=Depends(require_viewer)):
    """Enrolled face records of one person (metadata only - encodings are never returned)"""
    student = crud.get_live_student(db, student_id)
    if student is None:
        raise HTTPException(status_code=404, detail="Student not found")
    return [FaceRecord(id=e.id, is_active=e.is_active, created_at=e.created_at)
            for e in sorted(student.face_embeddings, key=lambda e: e.id)]


@router.delete("/{student_id}/{embedding_id}")
async def delete_face(student_id: int, embedding_id: int, db: Session = Depends(get_db),
                      current_user=Depends(require_operator)):
    """Permanently erase one enrolled face encoding (biometric data minimisation). The
    person's attendance history is not affected."""
    row = db.query(models.FaceEmbedding).filter(models.FaceEmbedding.id == embedding_id,
                                                models.FaceEmbedding.student_id == student_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Face record not found")
    audit(db, current_user, "face.delete", "student", student_id, {"embedding_id": embedding_id})
    db.delete(row)
    db.commit()
    notify_known_faces_changed()
    return {"message": "Face record deleted"}


@router.post("/{student_id}", response_model=EnrollmentResult)
async def enroll_face(
    student_id: int,
    file: UploadFile = File(...),
    confirm_warnings: bool = Query(False, description="Enroll even if the face resembles another person or "
                                                      "differs from this person's existing photos"),
    db: Session = Depends(get_db),
    current_user=Depends(require_operator)
):
    """
    Enroll a face for an existing student by uploading an image.
    Extracts face encoding and stores it in the database. The image itself is not stored.
    """
    # Validate file size
    contents = await file.read(MAX_FILE_SIZE + 1)
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)} MB"
        )

    # Reset file position for potential reuse
    await file.seek(0)

    # Validate file type
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image type. Allowed types: {', '.join(ALLOWED_IMAGE_TYPES)}"
        )

    # Check if student exists and is active
    student = crud.get_student(db, student_id)
    if not student or student.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found"
        )

    if not student.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot enroll face for inactive student"
        )

    try:
        image = decode_image(contents)
        return enroll_image(db, student, image, current_user, confirm_warnings=confirm_warnings)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error enrolling face for student {student_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during face enrollment"
        )


def decode_image(contents: bytes) -> np.ndarray:
    """Encoded image bytes -> BGR image (EXIF orientation applied by OpenCV); 400 if unusable"""
    image = cv2.imdecode(np.frombuffer(contents, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image data")
    if image.shape[0] * image.shape[1] > MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image dimensions are too large")
    return image


def analyse_photo(image: np.ndarray):
    """Validate one photo and encode it with the active recognition model, exactly as cameras
    will: exactly one face, quality checks, then the encoding. Writes nothing.
    Returns (model, encoding, quality); raises HTTPException with the reason otherwise.
    Used by the upload endpoint and by scripts/batch_enroll.py."""
    model = face_models.active_model()
    face_recognition = None
    if model == face_models.DLIB:
        # Imported lazily so the rest of the API still starts if face_recognition/dlib
        # (or its face_recognition_models -> pkg_resources dependency) is unavailable.
        # face_recognition calls quit() on a broken models package, hence BaseException.
        try:
            import face_recognition
        except BaseException as e:
            logger.error(f"face_recognition unavailable: {type(e).__name__}: {e}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Face recognition is not available on this server (face_recognition could not be imported)"
            )

    # Convert BGR to RGB (face_recognition expects RGB)
    rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # Detect face locations
    if model == face_models.SFACE:
        try:
            sface_faces = _sface().detect(image)
        except face_models.FaceModelUnavailable as e:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
        face_locations = [face_models.SFaceModel.location(f) for f in sface_faces]
    else:
        face_locations = face_recognition.face_locations(rgb_image)

    # Validate exactly one face
    if len(face_locations) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No face detected in the image"
        )

    if len(face_locations) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Multiple faces detected. Please provide an image with exactly one face"
        )

    quality = assess_face_quality(rgb_image, face_locations[0])
    problems = quality_problems(quality)
    if problems:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Photo not usable for enrollment: " + " ".join(problems))

    # Generate face encoding
    if model == face_models.SFACE:
        # Aligned on the eyes/nose/mouth landmarks, the same way cameras encode faces
        face_encodings = [_sface().embed(image, sface_faces[0])]
    else:
        # More re-sampling than the camera path: enrollment happens once per photo, and a
        # steadier reference encoding makes every later camera match more reliable
        face_encodings = face_recognition.face_encodings(rgb_image, face_locations,
                                                         num_jitters=settings.FACE_ENROLLMENT_JITTERS)

    if len(face_encodings) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not generate face encoding"
        )

    encoding = face_encodings[0]

    # Validate encoding has 128 elements (standard for face_recognition)
    if len(encoding) != 128:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Invalid face encoding size: {len(encoding)}. Expected 128 elements."
        )
    return model, np.asarray(encoding, dtype=np.float64), quality


def enroll_image(db: Session, student: models.Student, image: np.ndarray, actor,
                 confirm_warnings: bool = False) -> "EnrollmentResult":
    """Validate, encode and store one photo for an existing, active student (audited).
    The caller has checked the student; raises HTTPException with the reason on refusal
    (bad photo 400/422, resembles someone else 409, model unavailable 503)."""
    model, encoding, quality = analyse_photo(image)
    student_id = student.id
    warnings = identity_conflicts(db, student, encoding, model)
    if warnings and not confirm_warnings:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=" ".join(warnings) + " Enroll anyway only if you are sure "
                                                        "(confirm_warnings=true).")

    # Stored format identifies the model (app/face_models.py)
    embedding_bytes = face_models.encode(model, encoding)

    # Create face embedding record
    face_embedding_create = schemas.FaceEmbeddingCreate(
        embedding_data=embedding_bytes,
        is_active=True
    )

    # Save to database
    db_embedding = crud.create_face_embedding(
        db=db,
        embedding=face_embedding_create,
        student_id=student_id
    )

    audit(db, actor, "face.enroll", "student", student.id,
          {"embedding_id": db_embedding.id, "model": model, "quality": quality,
           "warnings_confirmed": bool(warnings)},
          commit=True)

    # Tell running camera workers to reload known faces
    notify_known_faces_changed()

    logger.info(f"Successfully enrolled face for student {student.student_id} (ID: {student.id})")

    return EnrollmentResult(id=db_embedding.id, student_id=db_embedding.student_id,
                            is_active=db_embedding.is_active, created_at=db_embedding.created_at,
                            updated_at=db_embedding.updated_at, quality=quality, warnings=warnings)
