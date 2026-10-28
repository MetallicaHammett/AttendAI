"""
Stricter recognition: faces too small or blurred to identify are skipped (never matched,
never counted as unregistered), a match needs a clear lead over the next-closest student,
and enrollment builds steadier reference encodings. No camera is opened; dlib calls are
replaced by stubs where a test needs fixed face boxes.
"""
import numpy as np
import pytest

from app import services
from app.config import settings
from app.services import DetectionStatus, FaceDetectionService, FaceObservationKind


def textured(h, w):
    """A sharp synthetic image (high Laplacian variance)"""
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


@pytest.fixture
def stub_dlib(monkeypatch):
    """face_locations returns the boxes set in `boxes`; face_encodings records which boxes
    were encoded and returns one distinct encoding per box"""
    import face_recognition
    state = {"boxes": [], "encoded": []}
    monkeypatch.setattr(face_recognition, "face_locations", lambda img, **kw: list(state["boxes"]))

    def encodings(img, locations, **kw):
        state["encoded"].extend(locations)
        state["jitters"] = kw.get("num_jitters")
        return [np.full(128, i, dtype=np.float64) for i, _ in enumerate(locations)]
    monkeypatch.setattr(face_recognition, "face_encodings", encodings)
    return state


def test_small_faces_are_skipped_before_matching(stub_dlib):
    frame = textured(300, 300)
    small, large = (10, 40, 40, 10), (50, 200, 200, 50)  # 30 px and 150 px faces
    stub_dlib["boxes"] = [small, large]
    result = FaceDetectionService(min_face_size=50, min_sharpness=0).detect_faces(frame, [(0, 0, 300, 300)])
    assert result.status == DetectionStatus.SUCCESS
    assert result.face_locations == [large] and len(result.face_encodings) == 1
    assert result.low_quality_count == 1
    assert stub_dlib["encoded"] == [large]  # the small face was never even encoded


def test_blurred_faces_are_skipped(stub_dlib):
    frame = textured(300, 300)
    frame[50:200, 50:200] = 128  # a flat (completely blurred) face region
    stub_dlib["boxes"] = [(50, 200, 200, 50)]
    result = FaceDetectionService(min_face_size=0, min_sharpness=15).detect_faces(frame, [(0, 0, 300, 300)])
    assert result.status == DetectionStatus.NO_FACE and result.low_quality_count == 1
    assert stub_dlib["encoded"] == []


def test_sharp_large_face_passes_and_checks_can_be_disabled(stub_dlib):
    frame = textured(300, 300)
    stub_dlib["boxes"] = [(50, 200, 200, 50)]
    assert FaceDetectionService(min_face_size=50, min_sharpness=15).detect_faces(
        frame, [(0, 0, 300, 300)]).low_quality_count == 0
    stub_dlib["boxes"] = [(10, 40, 40, 10)]
    tiny = FaceDetectionService().detect_faces(frame, [(0, 0, 300, 300)])  # defaults: no gate
    assert tiny.status == DetectionStatus.SUCCESS and tiny.low_quality_count == 0


def test_quality_measures():
    flat = np.full((100, 100, 3), 90, np.uint8)
    size, sharpness = FaceDetectionService.face_quality(flat, (10, 70, 90, 20))
    assert size == 50 and sharpness == 0.0
    assert FaceDetectionService.face_quality(textured(100, 100), (10, 70, 90, 20))[1] > 1000


def enc(x):
    v = np.zeros(128)
    v[0] = x
    return v


@pytest.mark.parametrize("runner_up,kind", [(0.33, FaceObservationKind.AMBIGUOUS), (0.40, FaceObservationKind.KNOWN)])
def test_best_match_needs_a_clear_lead_over_the_next_student(runner_up, kind):
    """Default margin: a look-alike within 0.06 makes the face ambiguous (never attributed)"""
    detector = FaceDetectionService(match_tolerance=settings.FACE_MATCH_TOLERANCE,
                                    ambiguity_margin=settings.FACE_MATCH_AMBIGUITY_MARGIN)
    match = detector.classify_encoding(enc(0.0), [enc(0.30), enc(runner_up)], [1, 2])
    assert match.kind == kind
    assert match.student_id == (1 if kind == FaceObservationKind.KNOWN else None)


def test_defaults_are_stricter_than_before():
    assert settings.FACE_MATCH_AMBIGUITY_MARGIN >= 0.05
    assert settings.FACE_MIN_SIZE_PIXELS >= 40 and settings.FACE_MIN_SHARPNESS > 0
    assert settings.FACE_ENROLLMENT_JITTERS > settings.FACE_ENCODING_JITTERS
    # Unchanged safeguards: not weakened by this work
    assert settings.ATTENDANCE_MIN_CONFIDENCE >= 70 and settings.ATTENDANCE_MIN_CONSECUTIVE_FRAMES >= 5


class _People:
    """Person detector stub: one person filling the frame"""
    load_error = None

    def detect_persons(self, frame):
        return services.DetectionResult(boxes=[(0, 0, frame.shape[1], frame.shape[0])], confidences=[0.9],
                                        status=DetectionStatus.SUCCESS, frame_shape=frame.shape)


def test_skipped_faces_reach_neither_attendance_nor_unregistered_count(stub_dlib, monkeypatch):
    """A worker sees only a too-small face for many frames: nothing is recorded, no
    unregistered visitor is counted, and the skip counter shows why"""
    stub_dlib["boxes"] = [(10, 40, 40, 10)]  # 30 px
    detector = FaceDetectionService(min_face_size=50, min_sharpness=0)
    detector.load_known_faces([(1, enc(0.0))])
    engine = services.AttendanceEngine()
    tracker = services.UnknownOccupancyTracker()
    svc = services.VideoProcessingService(video_source="0", camera_type="IN", camera_id=1, attendance_engine=engine,
                                          face_detector=detector, unknown_tracker=tracker, person_detector=_People())
    monkeypatch.setattr(svc.tracker, "update_tracks",
                        lambda frame, dets: services.TrackingResult(tracks=[], status=DetectionStatus.SUCCESS))
    for i in range(20):
        _, decisions = svc._process_frame(textured(240, 320), 1000.0 + i * 0.2, record_event=None)
        assert decisions == []
    stats = svc.get_stats()
    assert stats["consecutive_failures"] == 0 and svc.pipeline_error is None  # frames really processed
    assert stats["total_low_quality_faces"] == 20
    assert stats["total_unknown_seen"] == 0 and stats["total_known_recognitions"] == 0
    assert tracker.inside_count() == 0 and engine.inside_count() == 0


def test_enrollment_uses_more_resampling(api, stub_dlib, monkeypatch):
    """The enrollment endpoint encodes photos with FACE_ENROLLMENT_JITTERS"""
    import cv2
    client, factory = api
    from app import models
    db = factory()
    student = models.Student(student_id="E1", name="Enrol Test")
    db.add(student)
    db.commit()
    sid = student.id
    db.close()
    stub_dlib["boxes"] = [(40, 260, 260, 40)]  # 220 px face
    photo = textured(300, 300)
    ok, png = cv2.imencode(".png", photo)
    r = client.post(f"/face-enrollment/{sid}", files={"file": ("face.png", png.tobytes(), "image/png")})
    assert stub_dlib.get("jitters") == settings.FACE_ENROLLMENT_JITTERS, r.text
