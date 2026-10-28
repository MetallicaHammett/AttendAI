"""
Unit tests for face recognition logic.

Matching uses numpy Euclidean distances (same metric as face_recognition.face_distance),
so no face_recognition/dlib mocking is needed.
"""
import numpy as np
import sys
import os

# Add the current directory to the path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services import FaceDetectionService, RecognitionResult, DetectionStatus


def test_ambiguous_match_logic():
    """Test face equidistant (and within tolerance) from two different students"""
    service = FaceDetectionService()
    service.known_face_encodings = [np.array([0.4, 0, 0]), np.array([-0.4, 0, 0])]
    service.known_face_student_ids = [1, 2]  # Different student IDs

    result = service.recognize_faces([np.array([0.0, 0, 0])])  # Midway point, distance 0.4 to both

    # Ambiguous -> treated as unknown person
    assert result.status == DetectionStatus.UNKNOWN_PERSON
    assert len(result.student_ids) == 1
    assert result.student_ids[0] is None  # Should be None for ambiguous
    assert result.confidences[0] == 0.0   # Should be 0.0 confidence


def test_unambiguous_match_logic():
    """Test face that clearly matches one known face"""
    service = FaceDetectionService()
    service.known_face_encodings = [np.array([1, 0, 0]), np.array([-1, 0, 0])]
    service.known_face_student_ids = [1, 2]  # Different student IDs

    result = service.recognize_faces([np.array([0.8, 0, 0])])  # 0.2 from student 1, 1.8 from student 2

    assert result.status == DetectionStatus.SUCCESS
    assert result.student_ids == [1]
    assert abs(result.confidences[0] - 80.0) < 1e-9  # (1 - 0.2) * 100


def test_same_student_duplicate_logic():
    """Two identical enrollments of the same student are not ambiguous"""
    service = FaceDetectionService()
    service.known_face_encodings = [np.array([1, 0, 0]), np.array([1, 0, 0])]  # Identical
    service.known_face_student_ids = [1, 1]  # Same student

    result = service.recognize_faces([np.array([1, 0, 0])])

    assert result.status == DetectionStatus.SUCCESS
    assert result.student_ids == [1]
    assert result.confidences[0] == 100.0
