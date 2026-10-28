#!/usr/bin/env python3
"""
Video source test script for AttendAI
Tests webcam and video file ingestion capabilities
"""

import cv2
import time
import sys
from pathlib import Path

def test_webcam():
    """Test access to default webcam"""
    print("Testing webcam access...")

    # Try to open webcam (device index 0)
    cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("❌ Failed to open webcam (device index 0)")
        return False

    # Try to read a frame
    ret, frame = cap.read()
    if not ret or frame is None:
        print("❌ Webcam opened but failed to read frame")
        cap.release()
        return False

    print(f"✅ Webcam accessible - Frame shape: {frame.shape}")

    # Test reading multiple frames
    frame_count = 0
    start_time = time.time()

    for _ in range(30):  # Read 30 frames
        ret, frame = cap.read()
        if not ret or frame is None:
            print(f"❌ Failed to read frame {frame_count}")
            break
        frame_count += 1

    elapsed_time = time.time() - start_time
    fps = frame_count / elapsed_time if elapsed_time > 0 else 0

    print(f"✅ Read {frame_count} frames at ~{fps:.1f} FPS")

    # Get camera properties
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    actual_fps = cap.get(cv2.CAP_PROP_FPS)

    print(f"📷 Camera properties: {width}x{height} @ {actual_fps} FPS")

    cap.release()
    return True

def test_video_file(video_path):
    """Test access to a video file"""
    print(f"\nTesting video file: {video_path}")

    video_path = Path(video_path)
    if not video_path.exists():
        print(f"❌ Video file not found: {video_path}")
        return False

    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        print(f"❌ Failed to open video file: {video_path}")
        return False

    # Try to read a frame
    ret, frame = cap.read()
    if not ret or frame is None:
        print(f"❌ Video file opened but failed to read frame")
        cap.release()
        return False

    print(f"✅ Video file accessible - Frame shape: {frame.shape}")

    # Get video properties
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)

    print(f"🎬 Video properties: {width}x{height} @ {fps} FPS, {frame_count} total frames")

    # Test reading a few frames
    frame_count_read = 0
    start_time = time.time()

    for _ in range(min(30, int(frame_count))):  # Read up to 30 frames or total frames
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        frame_count_read += 1

    elapsed_time = time.time() - start_time
    read_fps = frame_count_read / elapsed_time if elapsed_time > 0 else 0

    print(f"✅ Read {frame_count_read} frames at ~{read_fps:.1f} FPS")

    cap.release()
    return True

def main():
    print("=" * 50)
    print("AttendAI Video Source Test")
    print("=" * 50)

    # Test webcam
    webcam_success = test_webcam()

    # Test for common video files in data directory
    data_dir = Path("data")
    video_extensions = [".mp4", ".avi", ".mov", ".mkv"]
    video_files = []

    if data_dir.exists():
        for ext in video_extensions:
            video_files.extend(data_dir.glob(f"*{ext}"))

    video_success = False
    if video_files:
        print(f"\nFound {len(video_files)} video file(s) in data/ directory:")
        for vf in video_files:
            print(f"  - {vf.name}")
        # Test the first video file found
        video_success = test_video_file(video_files[0])
    else:
        print("\n⚠️  No video files found in data/ directory")
        print("   To test video files, place MP4/AVI/MOV/MKV files in the data/ directory")

    print("\n" + "=" * 50)
    print("Test Summary:")
    print(f"  Webcam access: {'✅ PASS' if webcam_success else '❌ FAIL'}")
    print(f"  Video file access: {'✅ PASS' if video_success else '❌ FAIL (no files tested)'}")

    if webcam_success or video_success:
        print("\n🎉 Video source testing SUCCESSFUL")
        print("   AttendAI can ingest actual video on this machine.")
        return 0
    else:
        print("\n💥 Video source testing FAILED")
        print("   AttendAI cannot access video sources on this machine.")
        return 1

if __name__ == "__main__":
    sys.exit(main())