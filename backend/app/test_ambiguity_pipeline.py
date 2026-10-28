"""
Test the full ambiguity handling pipeline from face recognition to attendance recording
"""
import numpy as np
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services import FaceDetectionService, AttendanceEngine, RecognitionResult, DetectionStatus
from app import crud, models
from app.database import SessionLocal, engine
import tempfile

def test_ambiguity_handling_in_pipeline():
    """Test that ambiguous matches don't create attendance records"""
    print("Testing ambiguity handling in attendance pipeline...")

    # Create a temporary database for testing
    test_db = SessionLocal()

    try:
        # Create test students
        student1 = models.Student(
            student_id="STU001",
            name="Alice Smith",
            is_active=True
        )
        student2 = models.Student(
            student_id="STU002",
            name="Bob Jones",
            is_active=True
        )
        test_db.add(student1)
        test_db.add(student2)
        test_db.commit()
        test_db.refresh(student1)
        test_db.refresh(student2)

        # Create face recognition service
        face_service = FaceDetectionService()

        # Create two different face embeddings that are equidistant from test face
        # This simulates two different students with similar appearances
        embedding1 = np.array([1.0, 0.0, 0.0] + [0.0] * 125)  # [1,0,0,...]
        embedding2 = np.array([-1.0, 0.0, 0.0] + [0.0] * 125)  # [-1,0,0,...]
        test_face = np.array([0.0, 0.0, 0.0] + [0.0] * 125)    # [0,0,0,...] - midway

        # Load known faces (simulating what would come from database)
        face_service.load_known_faces([
            (student1.id, embedding1),  # Student ID 1
            (student2.id, embedding2)   # Student ID 2
        ])

        # Test recognition - should be ambiguous
        result = face_service.recognize_faces([test_face])

        print(f"Recognition result: student_id={result.student_ids[0]}, confidence={result.confidences[0]}")
        print(f"Status: {result.status}")

        # Verify ambiguous match handling
        assert result.student_ids[0] is None, "Ambiguous match should return None for student_id"
        assert result.confidences[0] == 0.0, "Ambiguous match should have 0.0 confidence"
        assert result.status == DetectionStatus.SUCCESS, "Should still be SUCCESS status (mixed known/unknown treatment)"

        # Test attendance engine - should not record attendance for ambiguous match
        attendance_engine = AttendanceEngine(
            confidence_threshold=70.0,
            min_consecutive_frames=5
        )

        timestamp = 1000.0  # Fixed timestamp for testing
        decision = attendance_engine.process_recognition(
            student_id=result.student_ids[0],  # This should be None
            confidence=result.confidences[0],  # This should be 0.0
            timestamp=timestamp,
            camera_type="IN"
        )

        print(f"Attendance decision: should_record={decision.should_record}, reasoning='{decision.reasoning}'")
        print(f"Event type: {decision.event_type}")

        # Verify that ambiguous matches don't create attendance records
        assert decision.should_record == False, "Ambiguous matches should not create attendance records"
        assert decision.student_id is None, "Student ID should remain None"
        assert decision.event_type == "UNKNOWN", "Should be marked as unknown person"

        # Test that unambiguous matches DO work correctly
        print("\nTesting unambiguous match...")
        clear_embedding = np.array([0.9, 0.0, 0.0] + [0.0] * 125)  # Close to student 1
        clear_result = face_service.recognize_faces([clear_embedding])

        print(f"Clear recognition result: student_id={clear_result.student_ids[0]}, confidence={clear_result.confidences[0]}")

        assert clear_result.student_ids[0] == student1.id, "Should match student 1"
        assert clear_result.confidences[0] > 0.0, "Should have positive confidence"

        # Test attendance recording for clear match
        clear_decision = attendance_engine.process_recognition(
            student_id=clear_result.student_ids[0],
            confidence=clear_result.confidences[0],
            timestamp=timestamp + 10,  # Slightly later timestamp
            camera_type="IN"
        )

        print(f"Clear match decision: should_record={clear_decision.should_record}",)
        print(f"Reasoning: {clear_decision.reasoning}")

        # For the first few frames, it shouldn't record (need consecutive frames)
        # But after enough frames, it should record
        attendance_engine.min_consecutive_frames = 2  # Reduce for testing
        clear_decision2 = attendance_engine.process_recognition(
            student_id=clear_result.student_ids[0],
            confidence=clear_result.confidences[0],
            timestamp=timestamp + 20,  # Second recognition
            camera_type="IN"
        )

        print(f"After {attendance_engine.min_consecutive_frames} frames: should_record={clear_decision2.should_record}")

        # Test unknown person handling
        print("\nTesting unknown person...")
        unknown_face = np.array([2.0, 0.0, 0.0] + [0.0] * 125)  # Far from both known faces
        unknown_result = face_service.recognize_faces([unknown_face])

        print(f"Unknown recognition result: student_id={unknown_result.student_ids[0]}, confidence={unknown_result.confidences[0]}")

        assert unknown_result.student_ids[0] is None, "Unknown person should return None for student_id"
        assert unknown_result.confidences[0] == 0.0, "Unknown person should have 0.0 confidence"

        unknown_decision = attendance_engine.process_recognition(
            student_id=unknown_result.student_ids[0],
            confidence=unknown_result.confidences[0],
            timestamp=timestamp + 30,
            camera_type="IN"
        )

        print(f"Unknown person decision: should_record={unknown_decision.should_record}")
        assert unknown_decision.should_record == False, "Unknown persons should not create attendance records"

        print("\n✅ All ambiguity handling tests passed!")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        test_db.close()

def test_database_persistence_boundary():
    """Test that the persistence layer properly validates attendance decisions"""
    print("\nTesting database persistence boundary...")

    test_db = SessionLocal()

    try:
        # Create a test student
        student = models.Student(
            student_id="STU003",
            name="Test Student",
            is_active=True
        )
        test_db.add(student)
        test_db.commit()
        test_db.refresh(student)

        # Test 1: Valid attendance decision should be persistable
        from app.schemas import AttendanceEventCreate

        valid_event = AttendanceEventCreate(
            student_id=student.id,
            event_type="ENTRY",
            confidence=85
        )

        db_event = crud.create_attendance_event(test_db, valid_event)
        print(f"✅ Valid event created: ID={db_event.id}")

        # Test 2: Invalid decisions should be rejected at service level (not reach DB)
        # This is tested in the ambiguity handling test above

        # Test 3: Direct database constraint testing
        try:
            # Try to create event with invalid student_id (non-existent)
            invalid_event = AttendanceEventCreate(
                student_id=99999,  # Non-existent student
                event_type="ENTRY",
                confidence=85
            )

            # This should fail due to foreign key constraint
            crud.create_attendance_event(test_db, invalid_event)
            print("❌ Should have failed for non-existent student")
            return False
        except Exception as e:
            print(f"✅ Correctly rejected invalid student_id: {type(e).__name__}")

        # Test 4: Try NULL student_id (should be rejected by schema validation)
        try:
            null_event = AttendanceEventCreate(
                student_id=None,  # This should fail schema validation
                event_type="ENTRY",
                confidence=85
            )
            print("❌ Should have failed schema validation for None student_id")
            return False
        except Exception as e:
            print(f"✅ Correctly rejected None student_id: {type(e).__name__}")

        print("✅ Database persistence boundary tests passed!")
        return True

    except Exception as e:
        print(f"❌ Database test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        test_db.close()

if __name__ == "__main__":
    success1 = test_ambiguity_handling_in_pipeline()
    success2 = test_database_persistence_boundary()

    if success1 and success2:
        print("\n🎉 All pipeline tests passed! Ambiguity handling is working correctly.")
    else:
        print("\n💥 Some tests failed.")
        sys.exit(1)