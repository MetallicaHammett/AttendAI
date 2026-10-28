"""
Test suite for AI Services - Face Recognition Ambiguity Handling
"""
import numpy as np
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services import FaceDetectionService, RecognitionResult, DetectionStatus


def test_recognize_faces_ambiguous_match():
    """Test that ambiguous matches (same distance to different students) return None"""
    # Create service with known faces
    service = FaceDetectionService()

    # Create two different face embeddings that are equidistant from the test face
    # We'll create embeddings such that the test face is exactly midway between them
    embedding1 = np.array([1.0, 0.0, 0.0] + [0.0] * 125)  # [1,0,0,...]
    embedding2 = np.array([-1.0, 0.0, 0.0] + [0.0] * 125)  # [-1,0,0,...]
    test_face = np.array([0.0, 0.0, 0.0] + [0.0] * 125)    # [0,0,0,...] - midway

    # Load known faces
    service.load_known_faces([
        (1, embedding1),  # Student ID 1
        (2, embedding2)   # Student ID 2
    ])

    # Recognize the test face
    result = service.recognize_faces([test_face])

    # Should return None for student_id (ambiguous) and 0.0 confidence
    assert result.status == DetectionStatus.UNKNOWN_PERSON  # Ambiguous match treated as unknown person
    assert len(result.student_ids) == 1
    assert result.student_ids[0] is None  # Ambiguous match
    assert result.confidences[0] == 0.0   # Zero confidence for ambiguous
    assert len(result.face_locations) == 0  # Face locations handled by caller


def test_recognize_faces_unambiguous_match():
    """Test that unambiguous matches work correctly"""
    # Create service with known faces
    service = FaceDetectionService()

    # Create a known face embedding
    known_embedding = np.array([1.0, 0.0, 0.0] + [0.0] * 125)  # [1,0,0,...]
    test_face = np.array([0.9, 0.0, 0.0] + [0.0] * 125)        # Close to known

    # Load known faces
    service.load_known_faces([
        (1, known_embedding)  # Student ID 1
    ])

    # Recognize the test face
    result = service.recognize_faces([test_face])

    # Should return the correct student ID with high confidence
    assert result.status == DetectionStatus.SUCCESS
    assert len(result.student_ids) == 1
    assert result.student_ids[0] == 1
    assert result.confidences[0] > 0.0  # Should have some confidence
    assert result.confidences[0] <= 100.0  # Confidence should be 0-100


def test_recognize_faces_unknown_person():
    """Test that unknown faces are handled correctly"""
    # Create service with known faces
    service = FaceDetectionService()

    # Create a known face embedding
    known_embedding = np.array([1.0, 0.0, 0.0] + [0.0] * 125)  # [1,0,0,...]
    test_face = np.array([-1.0, 0.0, 0.0] + [0.0] * 125)       # Far from known

    # Load known faces
    service.load_known_faces([
        (1, known_embedding)  # Student ID 1
    ])

    # Recognize the test face
    result = service.recognize_faces([test_face])

    # Should return None for student_id (unknown) and low confidence
    assert result.status == DetectionStatus.UNKNOWN_PERSON
    assert len(result.student_ids) == 1
    assert result.student_ids[0] is None  # Unknown person
    assert result.confidences[0] == 0.0   # Zero confidence for unknown


def test_recognize_faces_same_student_duplicate():
    """Test that duplicate embeddings for the same student are handled correctly"""
    # Create service with known faces
    service = FaceDetectionService()

    # Create two identical face embeddings for the same student
    embedding = np.array([1.0, 0.0, 0.0] + [0.0] * 125)  # [1,0,0,...]
    test_face = np.array([1.0, 0.0, 0.0] + [0.0] * 125)  # Identical to embedding

    # Load known faces - same student ID twice (duplicate enrollment)
    service.load_known_faces([
        (1, embedding),  # Student ID 1 - first enrollment
        (1, embedding)   # Student ID 1 - duplicate enrollment (same student)
    ])

    # Recognize the test face
    result = service.recognize_faces([test_face])

    # Should return the student ID (not ambiguous since it's the same student)
    assert result.status == DetectionStatus.SUCCESS
    assert len(result.student_ids) == 1
    assert result.student_ids[0] == 1  # Should match the student
    assert result.confidences[0] == 100.0  # Perfect match should give 100% confidence


def test_recognize_faces_multiple_faces():
    """Test recognition with multiple faces in one call"""
    # Create service with known faces
    service = FaceDetectionService()

    # Create known face embeddings
    embedding1 = np.array([1.0, 0.0, 0.0] + [0.0] * 125)  # [1,0,0,...] - Student 1
    embedding2 = np.array([0.0, 1.0, 0.0] + [0.0] * 125)  # [0,1,0,...] - Student 2

    # Create test faces
    test_face1 = np.array([0.9, 0.0, 0.0] + [0.0] * 125)  # Close to student 1
    test_face2 = np.array([0.0, 0.9, 0.0] + [0.0] * 125)  # Close to student 2

    # Load known faces
    service.load_known_faces([
        (1, embedding1),  # Student ID 1
        (2, embedding2)   # Student ID 2
    ])

    # Recognize multiple faces
    result = service.recognize_faces([test_face1, test_face2])

    # Should recognize both faces correctly
    assert result.status == DetectionStatus.SUCCESS
    assert len(result.student_ids) == 2
    assert result.student_ids[0] == 1  # First face -> student 1
    assert result.student_ids[1] == 2  # Second face -> student 2
    assert result.confidences[0] > 0.0
    assert result.confidences[1] > 0.0


def test_recognize_faces_mixed_results():
    """Test recognition with mixed known/unknown/ambiguous results"""
    # Create service with known faces
    service = FaceDetectionService()

    # Create known face embeddings
    embedding1 = np.array([1.0, 0.0, 0.0] + [0.0] * 125)  # [1,0,0,...] - Student 1
    embedding2 = np.array([-1.0, 0.0, 0.0] + [0.0] * 125) # [-1,0,0,...] - Student 2

    # Create test faces:
    # Face 1: Close to student 1 (should match)
    # Face 2: Midway between students 1 and 2 (ambiguous)
    # Face 3: Far from both (unknown)
    test_face1 = np.array([0.9, 0.0, 0.0] + [0.0] * 125)  # Close to student 1
    test_face2 = np.array([0.0, 0.0, 0.0] + [0.0] * 125)  # Midway -> ambiguous
    test_face3 = np.array([2.0, 0.0, 0.0] + [0.0] * 125)  # Far from both -> unknown

    # Load known faces
    service.load_known_faces([
        (1, embedding1),  # Student ID 1
        (2, embedding2)   # Student ID 2
    ])

    # Recognize multiple faces
    result = service.recognize_faces([test_face1, test_face2, test_face3])

    # Should get mixed results
    assert result.status == DetectionStatus.SUCCESS  # At least one known
    assert len(result.student_ids) == 3
    assert result.student_ids[0] == 1  # Known match
    assert result.student_ids[1] is None  # Ambiguous
    assert result.student_ids[2] is None  # Unknown
    assert result.confidences[0] > 0.0   # Known has confidence
    assert result.confidences[1] == 0.0  # Ambiguous has zero confidence
    assert result.confidences[2] == 0.0  # Unknown has zero confidence
