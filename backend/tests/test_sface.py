"""
SFace recognition (OpenCV YuNet + SFace). The real-model tests use the ONNX files in
backend/models/face and two photos already installed with other packages (matplotlib's
public-domain portrait of Grace Hopper, Ultralytics' sample photo of other people); they
are skipped if those files are absent. No camera is opened and nothing is downloaded.
"""
import os

import cv2
import numpy as np
import pytest

from app import face_models, models, services
from app.config import settings

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sample(path_parts):
    import importlib.util
    spec = importlib.util.find_spec(path_parts[0])
    if spec is None or not spec.submodule_search_locations:
        return None
    path = os.path.join(list(spec.submodule_search_locations)[0], *path_parts[1:])
    return path if os.path.isfile(path) else None


HOPPER = _sample(["matplotlib", "mpl-data", "sample_data", "grace_hopper.jpg"])
OTHERS = _sample(["ultralytics", "assets", "zidane.jpg"])
MODEL_FILES = [os.path.join(BACKEND, p) for p in (settings.FACE_SFACE_DETECTOR_PATH, settings.FACE_SFACE_RECOGNIZER_PATH)]
real_models = pytest.mark.skipif(not all(os.path.isfile(p) for p in MODEL_FILES) or not HOPPER or not OTHERS,
                                 reason="SFace model files or sample photos not installed")


# --- storage format and comparison ----------------------------------------------------

def test_stored_format_identifies_the_model():
    v = np.linspace(-1, 1, 128)
    dlib_bytes, sface_bytes = face_models.encode("dlib", v), face_models.encode("sface", v)
    assert (len(dlib_bytes), len(sface_bytes)) == (1024, 512)
    assert face_models.model_of(dlib_bytes) == "dlib" and face_models.model_of(sface_bytes) == "sface"
    assert face_models.model_of(b"\0" * 100) is None and face_models.decode(b"xx") == (None, None)
    model, back = face_models.decode(sface_bytes)
    assert model == "sface" and back.dtype == np.float64 and np.allclose(back, v, atol=1e-6)
    with pytest.raises(ValueError):
        face_models.encode("sface", np.zeros(64))


def test_sface_compares_by_cosine_distance():
    a, b = np.array([1.0, 0, 0]), np.array([0.0, 1, 0])
    assert np.allclose(face_models.distances("sface", [a, b, 2 * a], a), [0.0, 1.0, 0.0])
    assert np.allclose(face_models.distances("dlib", [b], a), [np.sqrt(2)])


def test_thresholds_per_model():
    sface = face_models.thresholds("sface")
    assert sface["match_tolerance"] == pytest.approx(1 - 0.363)
    assert sface["min_confidence"] == settings.FACE_SFACE_MIN_CONFIDENCE >= 50
    # Tracker limits are Euclidean on unit vectors: L2 = sqrt(2 x cosine distance)
    assert sface["unknown_same_person"] == pytest.approx(np.sqrt(2 * 0.637))
    assert sface["unknown_resolve_known"] < sface["unknown_same_person"]
    assert face_models.thresholds("dlib")["min_confidence"] == settings.ATTENDANCE_MIN_CONFIDENCE


def test_production_default_is_sface():
    from app.config import Settings
    assert Settings.model_fields["FACE_RECOGNITION_MODEL"].default == "sface"


def test_calibrated_defaults():
    """Calibrated on the class photos (2026-10-22): attendance needs cosine >= 0.60; the
    enrollment look-alike check refuses >= 0.55 to another student; match limit unchanged"""
    from app.config import Settings
    fields = Settings.model_fields
    assert fields["FACE_SFACE_MIN_CONFIDENCE"].default == 60.0
    assert fields["FACE_SFACE_LOOKALIKE_SIMILARITY"].default == 0.55
    assert fields["FACE_SFACE_MATCH_DISTANCE"].default == pytest.approx(0.637)
    assert face_models.thresholds("sface")["enroll_lookalike"] == pytest.approx(1 - settings.FACE_SFACE_LOOKALIKE_SIMILARITY)
    # dlib keeps its original single limit for both checks
    assert face_models.thresholds("dlib")["enroll_lookalike"] == settings.FACE_MATCH_TOLERANCE


def unit_at(cosine, base=None):
    """A unit vector with the given cosine similarity to `base` (default e0)"""
    base = np.eye(128)[0] if base is None else base
    other = np.eye(128)[1]
    return cosine * base + np.sqrt(1 - cosine ** 2) * other


@pytest.mark.parametrize("cosine_to_other,warned", [(0.50, False), (0.54, False), (0.56, True), (0.90, True)])
def test_enrollment_lookalike_uses_its_own_limit(api, sface_active, cosine_to_other, warned):
    from app.routers.face_enrollment import identity_conflicts
    _, factory = api
    db = factory()
    other, new = models.Student(student_id="O1", name="Other"), models.Student(student_id="N1", name="Newcomer")
    db.add_all([other, new])
    db.commit()
    add_face(db, other.id, "sface", np.eye(128)[0])
    warnings = identity_conflicts(db, new, unit_at(cosine_to_other), "sface")
    assert bool(warnings) == warned and (not warned or "similar to Other" in warnings[0])
    db.close()


@pytest.mark.parametrize("cosine_to_own,warned", [(0.40, False), (0.30, True)])
def test_own_photo_consistency_still_uses_the_match_limit(api, sface_active, cosine_to_own, warned):
    from app.routers.face_enrollment import identity_conflicts
    _, factory = api
    db = factory()
    me = models.Student(student_id="M1", name="Me")
    db.add(me)
    db.commit()
    add_face(db, me.id, "sface", np.eye(128)[0])
    warnings = identity_conflicts(db, me, unit_at(cosine_to_own), "sface")
    assert bool(warnings) == warned and (not warned or "does not match" in warnings[0])
    db.close()


def test_unknown_model_name_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "FACE_RECOGNITION_MODEL", "arcface-typo")
    with pytest.raises(ValueError):
        face_models.active_model()


# --- the real models --------------------------------------------------------------------

@pytest.fixture(scope="module")
def sface():
    return face_models.SFaceModel()


def faces_in(model, path, transform=None):
    img = cv2.imread(path)
    if transform:
        img = transform(img)
    return img, model.detect(img)


@real_models
def test_real_model_same_person_scores_high_different_people_low(sface):
    img, faces = faces_in(sface, HOPPER)
    assert len(faces) == 1
    reference = sface.embed(img, faces[0])
    assert np.linalg.norm(reference) == pytest.approx(1.0)
    # Same photo darker, smaller and mirrored: still clearly the same person
    for transform in (lambda i: cv2.convertScaleAbs(i, alpha=0.7, beta=-20),
                      lambda i: cv2.resize(i, None, fx=0.5, fy=0.5),
                      lambda i: cv2.flip(i, 1)):
        other_img, other_faces = faces_in(sface, HOPPER, transform)
        assert len(other_faces) == 1
        confidence = (1 - face_models.distances("sface", [reference], sface.embed(other_img, other_faces[0]))[0]) * 100
        assert confidence >= settings.FACE_SFACE_MIN_CONFIDENCE, confidence
    # Different people: below even the candidate-match threshold
    img2, faces2 = faces_in(sface, OTHERS)
    assert len(faces2) >= 1
    for face in faces2:
        d = face_models.distances("sface", [reference], sface.embed(img2, face))[0]
        assert d > settings.FACE_SFACE_MATCH_DISTANCE, d


@real_models
def test_camera_path_recognises_the_enrolled_person_and_not_others(sface):
    detector = services.FaceDetectionService(recognition_model="sface", sface_model=sface,
                                             match_tolerance=face_models.thresholds("sface")["match_tolerance"],
                                             ambiguity_margin=settings.FACE_MATCH_AMBIGUITY_MARGIN,
                                             min_face_size=settings.FACE_MIN_SIZE_PIXELS,
                                             min_sharpness=settings.FACE_MIN_SHARPNESS)
    img, faces = faces_in(sface, HOPPER)
    detector.load_known_faces([(7, sface.embed(img, faces[0]))])

    frame = cv2.convertScaleAbs(cv2.imread(HOPPER), alpha=0.8, beta=10)
    result = detector.detect_faces(frame, [(0, 0, frame.shape[1], frame.shape[0])])
    assert result.status == services.DetectionStatus.SUCCESS and len(result.face_encodings) == 1
    top, right, bottom, left = result.face_locations[0]
    assert 0 <= left < right <= frame.shape[1] and 0 <= top < bottom <= frame.shape[0]
    recognised = detector.recognize_faces(result.face_encodings)
    assert recognised.student_ids == [7]
    assert recognised.confidences[0] >= settings.FACE_SFACE_MIN_CONFIDENCE

    others = cv2.imread(OTHERS)
    result = detector.detect_faces(others, [(0, 0, others.shape[1], others.shape[0])])
    kinds = detector.recognize_faces(result.face_encodings).kinds
    assert kinds and all(k == services.FaceObservationKind.UNKNOWN for k in kinds)


@real_models
def test_quality_gate_applies_to_sface_too(sface):
    img = cv2.imread(HOPPER)
    tiny = cv2.resize(img, None, fx=0.12, fy=0.12)  # face well under 50 px
    detector = services.FaceDetectionService(recognition_model="sface", sface_model=sface, min_face_size=50)
    result = detector.detect_faces(tiny, [(0, 0, tiny.shape[1], tiny.shape[0])])
    if result.low_quality_count == 0:
        pytest.skip("YuNet did not find the face at this size at all")
    assert result.status == services.DetectionStatus.NO_FACE and result.face_encodings == []


def test_missing_model_files_are_a_clear_error(tmp_path):
    model = face_models.SFaceModel(detector_path=str(tmp_path / "nope.onnx"), recognizer_path=str(tmp_path / "x.onnx"))
    with pytest.raises(face_models.FaceModelUnavailable, match="missing"):
        model.check()
    detector = services.FaceDetectionService(recognition_model="sface", sface_model=model)
    frame = np.zeros((100, 100, 3), np.uint8)
    assert detector.detect_faces(frame, [(0, 0, 100, 100)]).status == services.DetectionStatus.PROCESSING_ERROR


# --- enrollment, counts and loading with SFace active ---------------------------------

def add_face(db, student_id, model, vector):
    db.add(models.FaceEmbedding(student_id=student_id, embedding_data=face_models.encode(model, vector), is_active=True))
    db.commit()


@real_models
def test_enrollment_stores_an_sface_encoding_and_counts_only_usable_faces(api, sface_active):
    client, factory = api
    db = factory()
    hopper, other = models.Student(student_id="GH1", name="Grace"), models.Student(student_id="OLD1", name="Old Enrol")
    db.add_all([hopper, other])
    db.commit()
    add_face(db, other.id, "dlib", np.random.default_rng(1).normal(size=128))  # enrolled before the switch
    ids = (hopper.id, other.id)
    db.close()

    with open(HOPPER, "rb") as f:
        r = client.post(f"/face-enrollment/{ids[0]}", files={"file": ("hopper.jpg", f.read(), "image/jpeg")})
    assert r.status_code == 200, r.text
    db = factory()
    row = db.query(models.FaceEmbedding).filter_by(student_id=ids[0]).one()
    assert face_models.model_of(row.embedding_data) == "sface"
    # Only faces the active model can use count as enrolled
    by_id = {s["id"]: s for s in client.get("/students/").json()}
    assert by_id[ids[0]]["enrolled_faces"] == 1 and by_id[ids[1]]["enrolled_faces"] == 0
    entry = db.query(models.AuditLog).filter_by(action="face.enroll").one()
    assert '"model": "sface"' in entry.details
    db.close()

    # A camera worker loads only the SFace face; the old dlib one is reported, not mixed in
    worker = services.VideoProcessingService(video_source="0", camera_type="IN", camera_id=1,
                                             face_detector=services.FaceDetectionService(recognition_model="sface"))
    db = factory()
    assert worker.load_known_faces_from_db(db)
    db.close()
    assert worker.face_detector.known_face_student_ids == [ids[0]]
    assert worker.faces_needing_reenrollment == 1


@real_models
def test_enrolling_the_same_face_for_a_second_person_is_flagged(api, sface_active):
    client, factory = api
    db = factory()
    a, b = models.Student(student_id="A1", name="First"), models.Student(student_id="B1", name="Second")
    db.add_all([a, b])
    db.commit()
    ids = (a.id, b.id)
    db.close()
    photo = open(HOPPER, "rb").read()
    assert client.post(f"/face-enrollment/{ids[0]}", files={"file": ("p.jpg", photo, "image/jpeg")}).status_code == 200
    r = client.post(f"/face-enrollment/{ids[1]}", files={"file": ("p.jpg", photo, "image/jpeg")})
    assert r.status_code == 409 and "similar to First" in r.json()["detail"]


def test_enrollment_with_missing_sface_files_is_503(api, sface_active, monkeypatch, tmp_path):
    from app.routers import face_enrollment
    client, factory = api
    db = factory()
    s = models.Student(student_id="M1", name="Missing Model")
    db.add(s)
    db.commit()
    sid = s.id
    db.close()
    monkeypatch.setattr(face_enrollment, "_SFACE", face_models.SFaceModel(detector_path=str(tmp_path / "a.onnx"),
                                                                         recognizer_path=str(tmp_path / "b.onnx")))
    ok, png = cv2.imencode(".png", np.full((200, 200, 3), 120, np.uint8))
    r = client.post(f"/face-enrollment/{sid}", files={"file": ("x.png", png.tobytes(), "image/png")})
    assert r.status_code == 503 and "missing" in r.json()["detail"]


def test_dashboard_enrolled_count_uses_the_active_model(api, sface_active):
    client, factory = api
    db = factory()
    s1, s2 = models.Student(student_id="S1", name="New"), models.Student(student_id="S2", name="Old")
    db.add_all([s1, s2])
    db.commit()
    add_face(db, s1.id, "sface", np.ones(128) / np.sqrt(128))
    add_face(db, s2.id, "dlib", np.zeros(128))
    db.close()
    assert client.get("/dashboard/stats").json()["enrolled_people"] == 1


def test_camera_start_uses_the_sface_thresholds(api, sface_active, monkeypatch):
    client, factory = api
    db = factory()
    cam = models.Camera(name="Door", source="0", source_type="webcam", camera_type="IN")
    db.add(cam)
    db.commit()
    cam_id = cam.id
    db.close()
    seen = {}

    class Capturing(services.VideoProcessingService):
        def __init__(self, **kwargs):
            seen.update(kwargs)
            super().__init__(**kwargs)

        def start(self):
            raise services.CameraOpenError("test: not opened")

    monkeypatch.setattr(services, "VideoProcessingService", Capturing)
    monkeypatch.setattr(services.PersonDetectionService, "check_model_file", lambda self: None)
    monkeypatch.setattr(face_models.SFaceModel, "check", lambda self: None)
    services.reset_shared_attendance_engine()
    assert client.post(f"/video/{cam_id}/start").status_code == 503
    detector = seen["face_detector"]
    assert detector.recognition_model == "sface" and detector.sface is not None
    assert detector.match_tolerance == pytest.approx(settings.FACE_SFACE_MATCH_DISTANCE)
    assert seen["attendance_engine"].confidence_threshold == settings.FACE_SFACE_MIN_CONFIDENCE


def test_camera_start_with_missing_sface_files_is_a_clear_503(api, sface_active, monkeypatch, tmp_path):
    client, factory = api
    db = factory()
    cam = models.Camera(name="Door", source="0", source_type="webcam", camera_type="IN")
    db.add(cam)
    db.commit()
    cam_id = cam.id
    db.close()
    monkeypatch.setattr(services.PersonDetectionService, "check_model_file", lambda self: None)
    monkeypatch.setattr(settings, "FACE_SFACE_RECOGNIZER_PATH", str(tmp_path / "gone.onnx"))
    r = client.post(f"/video/{cam_id}/start")
    assert r.status_code == 503 and "missing" in r.json()["detail"]
