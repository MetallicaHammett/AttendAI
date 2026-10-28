"""
Live-camera readiness fixes (fakes only - no webcam, file or stream is ever opened):
- live feeds always process the newest frame instead of a growing backlog
- frames are scaled without distorting faces (16:9 CCTV into a 4:3 working size)
- network streams open with bounded timeouts and RTSP over TCP
- face detection upsampling is configurable for small/distant faces
- the camera start endpoint wires these settings through
- the connection test uses the same bounded timeouts
- an admin edit of a camera event is relabelled as manual
- the person-detection library stays offline (no telemetry/analytics)
"""
import itertools
import os
import threading
import time

import numpy as np
import pytest

from app import models, services
from app.config import settings
from app.routers import cameras as cameras_router


# --- frame scaling ----------------------------------------------------------------------

@pytest.mark.parametrize("shape,expected", [
    ((1080, 1920, 3), (360, 640)),   # 16:9 CCTV into 640x480: no squashing
    ((480, 640, 3), (480, 640)),     # already the configured size: untouched
    ((240, 320, 3), (480, 640)),     # same aspect: scaled up as before
    ((1920, 1080, 3), (480, 270)),   # portrait camera
])
def test_frames_keep_their_aspect_ratio(shape, expected):
    svc = services.VideoProcessingService(video_source="x.mp4", frame_width=640, frame_height=480, source_type="video")
    out = svc.fit_to_frame_size(np.zeros(shape, np.uint8))
    assert out.shape[:2] == expected
    assert abs(out.shape[1] / out.shape[0] - shape[1] / shape[0]) < 0.01


# --- capture opening --------------------------------------------------------------------

class RecordingCapture:
    def __init__(self, *args):
        self.args = args
        self.released = False

    def isOpened(self):
        return True

    def set(self, *args):
        return True

    def read(self):
        return False, None

    def release(self):
        self.released = True


def test_streams_open_with_bounded_timeouts_and_rtsp_over_tcp(monkeypatch):
    opened = []
    monkeypatch.setattr(services.cv2, "VideoCapture", lambda *a: opened.append(a) or RecordingCapture(*a))
    stream = services.VideoProcessingService(video_source="rtsp://user:pw@10.0.0.5/stream1", source_type="rtsp",
                                             open_timeout_seconds=7, read_timeout_seconds=3)
    stream._open_capture()
    source, backend, params = opened[0]
    assert backend == services.cv2.CAP_FFMPEG
    assert dict(zip(params[::2], params[1::2])) == {services.cv2.CAP_PROP_OPEN_TIMEOUT_MSEC: 7000,
                                                     services.cv2.CAP_PROP_READ_TIMEOUT_MSEC: 3000}
    services.VideoProcessingService(video_source="0", source_type="webcam")._open_capture()
    assert opened[1] == (0,)  # local devices keep the plain call
    assert "rtsp_transport;tcp" in os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")


def test_connection_test_uses_bounded_timeouts_for_streams(monkeypatch):
    import cv2
    opened = []
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a: opened.append(a) or RecordingCapture(*a))
    monkeypatch.setattr(cameras_router, "CONNECTION_TEST_TIMEOUT_SECONDS", 0.05)
    from app import rtsp_check  # pre-check passes; this test is about how OpenCV is then called
    monkeypatch.setattr(rtsp_check, "check", lambda url, timeout=5.0: rtsp_check.RtspCheck(True, "ok", "test"))
    ok, detail, _ = cameras_router.probe_source("rtsp://u:p@10.0.0.5/s", 640, 480)
    assert not ok and len(opened[0]) == 3 and opened[0][1] == cv2.CAP_FFMPEG
    cameras_router.probe_source("1", 640, 480)
    assert opened[1] == (1,)


# --- latest-frame reader ----------------------------------------------------------------

class LiveFeed:
    """Fake live camera: every read returns the next frame, stamped with its number in the
    bottom-right pixel, at roughly 200 frames/second"""
    instances = []

    def __init__(self, *args, frames=None):
        self.counter = itertools.count(1)
        self.limit = frames  # None = endless; otherwise the stream dies after this many frames
        self.delivered = 0
        self.released = threading.Event()
        LiveFeed.instances.append(self)

    def isOpened(self):
        return True

    def set(self, *args):
        return True

    def read(self):
        time.sleep(0.005)
        if self.limit is not None and self.delivered >= self.limit:
            return False, None
        n = next(self.counter)
        self.delivered = n
        frame = np.zeros((48, 64, 3), np.uint8)
        frame[-1, -1, 0], frame[-1, -1, 1] = n % 256, n // 256
        return True, frame

    def release(self):
        self.released.set()


def marker(frame):
    return int(frame[-1, -1, 0]) + 256 * int(frame[-1, -1, 1])


def slow_pipeline(svc, seen, delay=0.12):
    def fake_process(frame, timestamp, record_event):
        seen.append(marker(frame))
        time.sleep(delay)  # recognition on a CPU
        return frame, []
    svc._process_frame = fake_process


@pytest.fixture
def live_feeds(monkeypatch):
    queue = []
    LiveFeed.instances = []
    monkeypatch.setattr(services.cv2, "VideoCapture", lambda *a: LiveFeed(*a, **(queue.pop(0) if queue else {})))
    return queue


def test_live_feed_processes_the_newest_frame_not_a_backlog(live_feeds):
    svc = services.VideoProcessingService(video_source="rtsp://10.0.0.5/s", frame_width=64, frame_height=48,
                                          source_type="rtsp", latest_frame_only=True)
    seen = []
    slow_pipeline(svc, seen)
    svc.start()
    try:
        for _ in range(4):
            assert svc.process_frame(None)[0] is not None
        feed = LiveFeed.instances[0]
        assert all(b > a for a, b in zip(seen, seen[1:]))
        assert min(b - a for a, b in zip(seen, seen[1:])) > 5  # old frames were skipped, not queued
        assert feed.delivered - seen[-1] < 40                  # the last processed frame was current
        stats = svc.get_stats()
        assert stats["latest_frame_only"] is True and stats["frames_skipped"] > 10
    finally:
        svc.stop()
    assert LiveFeed.instances[0].released.wait(2)  # the reader thread released the capture


def test_without_latest_mode_frames_are_read_in_order(live_feeds):
    """Previous behaviour, still used for recorded files: every frame, in order"""
    svc = services.VideoProcessingService(video_source="rtsp://10.0.0.5/s", frame_width=64, frame_height=48,
                                          source_type="rtsp", latest_frame_only=False)
    seen = []
    slow_pipeline(svc, seen, delay=0.01)
    svc.start()
    for _ in range(3):
        svc.process_frame(None)
    svc.stop()
    assert seen == [1, 2, 3]


def test_recorded_files_never_skip_frames(live_feeds):
    svc = services.VideoProcessingService(video_source="data/videos/clip.mp4", frame_width=64, frame_height=48,
                                          source_type="video", latest_frame_only=True)
    seen = []
    slow_pipeline(svc, seen, delay=0.01)
    svc.start()
    for _ in range(3):
        svc.process_frame(None)
    svc.stop()
    assert seen == [1, 2, 3] and svc.get_stats()["latest_frame_only"] is False


def test_latest_mode_detects_a_dropped_stream_and_reconnects(live_feeds):
    live_feeds.extend([{"frames": 3}, {}])  # first stream dies after 3 frames, the reconnect works
    svc = services.VideoProcessingService(video_source="rtsp://u:pw@10.0.0.5/s", frame_width=64, frame_height=48,
                                          source_type="rtsp", latest_frame_only=True, auto_reconnect=True)
    svc.read_wait_seconds = 0.02
    seen = []
    slow_pipeline(svc, seen, delay=0.0)
    svc.start()
    try:
        for _ in range(60):
            svc.process_frame(None)
            if svc.state == "reconnecting":
                break
        stats = svc.get_stats()
        assert stats["state"] == "reconnecting" and "pw" not in (stats["last_error"] or "")
        assert LiveFeed.instances[0].released.wait(2)
        svc._next_reconnect_at = 0
        svc.process_frame(None)  # reopens and attaches a new reader
        for _ in range(20):
            if svc.process_frame(None)[0] is not None:
                break
        assert svc.state == "running" and svc.get_stats()["total_reconnects"] == 1
    finally:
        svc.stop()


# --- face detection upsampling ----------------------------------------------------------

def test_face_detection_upsampling_is_configurable(monkeypatch):
    import face_recognition
    calls = []
    monkeypatch.setattr(face_recognition, "face_locations", lambda img, **kw: calls.append(kw) or [])
    frame = np.zeros((200, 200, 3), np.uint8)
    services.FaceDetectionService(upsample=2).detect_faces(frame, [(0, 0, 200, 200)])
    services.FaceDetectionService().detect_faces(frame, [(0, 0, 200, 200)])
    assert calls[0]["number_of_times_to_upsample"] == 2 and calls[1]["number_of_times_to_upsample"] == 1


# --- the start endpoint wires the settings through ---------------------------------------

def test_camera_start_uses_live_readiness_settings(api, monkeypatch):
    client, factory = api
    db = factory()
    cam = models.Camera(name="Gate IN", source="rtsp://u:pw@10.0.0.5/s1", source_type="rtsp", camera_type="IN")
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
    from app import rtsp_check  # the made-up address must not be contacted
    monkeypatch.setattr(rtsp_check, "check", lambda url, timeout=5.0: rtsp_check.RtspCheck(True, "ok", "test"))
    r = client.post(f"/video/{cam_id}/start")
    assert r.status_code == 503 and "pw" not in r.text
    assert seen["latest_frame_only"] is settings.CAMERA_LATEST_FRAME_ONLY is True
    assert seen["open_timeout_seconds"] == settings.CAMERA_OPEN_TIMEOUT_SECONDS
    assert seen["read_timeout_seconds"] == settings.CAMERA_READ_TIMEOUT_SECONDS
    assert seen["face_detector"].upsample == settings.FACE_DETECTION_UPSAMPLE
    assert seen["face_detector"].min_face_size == settings.FACE_MIN_SIZE_PIXELS > 0
    assert seen["face_detector"].min_sharpness == settings.FACE_MIN_SHARPNESS > 0
    assert seen["face_detector"].ambiguity_margin == settings.FACE_MATCH_AMBIGUITY_MARGIN > 0
    assert seen["camera_type"] == "IN" and seen["camera_id"] == cam_id


# --- attendance integrity -----------------------------------------------------------------

def test_admin_edit_of_a_camera_event_is_relabelled_manual(api):
    client, factory = api
    db = factory()
    a = models.Student(student_id="A1", name="Asha")
    b = models.Student(student_id="B2", name="Bala")
    db.add_all([a, b])
    db.commit()
    ev = models.AttendanceEvent(student_id=a.id, event_type="ENTRY", confidence=82, source="camera")
    db.add(ev)
    db.commit()
    ev_id, a_id, b_id = ev.id, a.id, b.id
    db.close()

    same = client.put(f"/attendance/{ev_id}", json={"student_id": a_id, "event_type": "ENTRY", "confidence": 82})
    assert same.status_code == 200 and same.json()["source"] == "camera"  # nothing changed: still a camera event
    r = client.put(f"/attendance/{ev_id}", json={"student_id": b_id, "event_type": "ENTRY", "confidence": 82})
    assert r.status_code == 200 and r.json()["source"] == "manual" and "edited by admin" in r.json()["note"]
    entry = client.get("/users/audit-log").json()[0]
    assert entry["action"] == "attendance.update" and entry["details"]["before"]["source"] == "camera"


# --- tracker and model warm-up ------------------------------------------------------------

def test_tracker_works_with_the_installed_numpy():
    """deep-sort-realtime 1.0 uses np.float; every update used to fail with NumPy >= 1.24"""
    tracker = services.TrackingService()
    frame = np.zeros((480, 640, 3), np.uint8)
    statuses = [tracker.update_tracks(frame, [(100, 100, 220, 400, 0.9)]).status for _ in range(5)]
    assert services.DetectionStatus.PROCESSING_ERROR not in statuses, tracker._init_error
    assert statuses[-1] == services.DetectionStatus.SUCCESS  # a confirmed track after a few frames


def test_warm_up_loads_models_once_without_touching_camera_state():
    calls = {"person": 0, "face": 0, "track": 0}
    svc = services.VideoProcessingService(video_source="0", source_type="webcam", frame_width=64, frame_height=48)

    def person(frame):
        calls["person"] += 1
        return services.DetectionResult([], [], services.DetectionStatus.NO_PERSON, frame.shape)

    def face(frame, boxes):
        calls["face"] += 1
        return services.FaceResult([], [], services.DetectionStatus.NO_FACE, boxes)

    def track(frame, dets):
        calls["track"] += 1
        return services.TrackingResult(tracks=[], status=services.DetectionStatus.NO_PERSON)

    svc.person_detector.detect_persons = person
    svc.face_detector.detect_faces = face
    svc.tracker.update_tracks = track
    assert svc.warm_up() >= 0
    assert calls == {"person": 1, "face": 1, "track": 1}
    stats = svc.get_stats()
    assert svc.frame_count == 0 and stats["avg_processing_ms"] is None and not svc.recent_observations


def test_warm_up_failure_does_not_stop_the_camera():
    svc = services.VideoProcessingService(video_source="0", source_type="webcam")
    svc.person_detector.detect_persons = lambda frame: (_ for _ in ()).throw(RuntimeError("model broken"))
    assert svc.warm_up() >= 0  # logged; the first real frame reports the problem as pipeline_error


# --- on-premise ---------------------------------------------------------------------------

def test_person_detection_library_runs_offline():
    assert os.environ.get("YOLO_OFFLINE") == "1"
    from ultralytics import utils
    assert utils.ONLINE is False
