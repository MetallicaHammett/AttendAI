"""
Camera reliability and safety: reconnect with backoff, end of recorded files, stale-feed
detection, credential masking, source validation, one session per physical source,
missing model files, and the connection test.

cv2.VideoCapture is replaced by fakes in every test that would open a source, so no
webcam, file or stream is ever touched.
"""
import os
import time

import numpy as np
import pytest

from app import models, services
from app.config import settings
from app.routers import cameras as cameras_router
from app.sources import mask_source, restore_credentials, validate_source
from helpers import add_camera

FRAME = np.zeros((48, 64, 3), np.uint8)


class ScriptedCapture:
    """Fake capture: `opens` decides isOpened(); reads follow `script` (True = frame)"""
    instances = []

    def __init__(self, source, opens=True, script=None):
        self.source, self.opens, self.script = source, opens, list(script or [])
        self.released = False
        ScriptedCapture.instances.append(self)

    def isOpened(self):
        return self.opens

    def set(self, *args):
        return True

    def read(self):
        ok = self.script.pop(0) if self.script else False
        return (True, FRAME.copy()) if ok else (False, None)

    def release(self):
        self.released = True


@pytest.fixture
def captures(monkeypatch):
    """Queue of fake captures handed out by cv2.VideoCapture, in order"""
    queue = []
    ScriptedCapture.instances = []

    def factory(source, *args):
        spec = queue.pop(0) if queue else {"opens": False}
        return ScriptedCapture(source, **spec)

    monkeypatch.setattr(services.cv2, "VideoCapture", factory)
    monkeypatch.setattr(services.PersonDetectionService, "detect_persons",
                        lambda self, frame: services.DetectionResult([], [], services.DetectionStatus.NO_PERSON, frame.shape))
    return queue


def make_service(**kwargs):
    params = dict(video_source="rtsp://user:pw@10.1.1.1/live", frame_width=64, frame_height=48,
                  source_type="rtsp", auto_reconnect=True)
    params.update(kwargs)
    return services.VideoProcessingService(**params)


def test_live_source_reconnects_after_drop(captures):
    captures.extend([{"script": [True, True]}, {"opens": False}, {"script": [True]}])
    svc = make_service()
    svc.start()
    assert svc.process_frame(None)[0] is not None and svc.process_frame(None)[0] is not None

    for _ in range(svc.max_consecutive_failures):  # the stream drops
        assert svc.process_frame(None) == (None, [])
    stats = svc.get_stats()
    assert stats["state"] == "reconnecting" and stats["camera_healthy"] is False
    assert "pw" not in stats["last_error"] and "***@" in stats["last_error"]
    assert ScriptedCapture.instances[0].released

    svc._next_reconnect_at = 0  # backoff elapsed
    svc.process_frame(None)     # attempt 1 fails -> rescheduled with a longer delay
    assert svc.get_stats()["reconnect_attempts"] == 1 and svc.state == "reconnecting"
    assert svc._next_reconnect_at > time.time()

    svc._next_reconnect_at = 0
    svc.process_frame(None)     # attempt 2 succeeds
    frame, _ = svc.process_frame(None)
    stats = svc.get_stats()
    assert frame is not None and stats["state"] == "running" and stats["last_error"] is None
    assert stats["total_reconnects"] == 1 and stats["camera_healthy"] is True


def test_reconnect_is_off_unless_enabled(captures):
    captures.append({"script": [True]})
    svc = make_service(auto_reconnect=False)
    svc.start()
    svc.process_frame(None)
    for _ in range(svc.max_consecutive_failures + 3):
        svc.process_frame(None)
    assert svc.get_stats()["state"] == "disconnected"
    assert len(ScriptedCapture.instances) == 1  # never reopened


def test_recorded_file_reports_end_instead_of_error(captures):
    captures.append({"script": [True, True]})
    svc = make_service(video_source="data/videos/demo.mp4", source_type="video")
    svc.start()
    svc.process_frame(None)
    svc.process_frame(None)
    assert svc.process_frame(None) == (None, [])
    stats = svc.get_stats()
    assert stats["state"] == "ended" and stats["feed_kind"] == "recorded" and stats["last_error"] is None


def test_stale_feed_is_reported(captures):
    captures.append({"script": [True]})
    svc = make_service(stale_after_seconds=5)
    svc.start()
    svc.process_frame(None)
    assert svc.get_stats()["stale"] is False
    svc.last_frame_at = time.time() - 30
    stats = svc.get_stats()
    assert stats["stale"] is True and stats["frame_age_seconds"] >= 30


def test_open_failure_message_never_contains_credentials(captures):
    svc = make_service()
    with pytest.raises(services.CameraOpenError) as exc:
        svc.start()
    assert "pw" not in str(exc.value) and "rtsp://***@10.1.1.1/live" in str(exc.value)


def test_feed_kinds_label_sources_honestly():
    assert make_service(source_type="webcam", video_source="0").feed_kind == "live"
    assert make_service(source_type="video", video_source="x.mp4").feed_kind == "recorded"
    synthetic = make_service(source_type="webcam", video_source="0")
    synthetic.use_test_source = True
    assert synthetic.feed_kind == "synthetic"


def test_missing_model_is_a_setup_error_not_a_download(tmp_path):
    detector = services.PersonDetectionService(model_path=str(tmp_path / "absent.pt"))
    with pytest.raises(services.ModelUnavailableError, match="never downloads"):
        detector.check_model_file()
    result = detector.detect_persons(FRAME)
    assert result.status == services.DetectionStatus.PROCESSING_ERROR and "not found" in detector.load_error


def test_ultralytics_runs_offline():
    assert os.environ.get("YOLO_OFFLINE") == "1"


# --- source helpers ----------------------------------------------------------------------

def test_mask_and_restore_credentials():
    url = "rtsp://admin:p%40ss@cam.local:554/Streaming/101"
    masked = mask_source(url)
    assert masked == "rtsp://***@cam.local:554/Streaming/101"
    assert restore_credentials(masked.replace("101", "102"), url) == url.replace("101", "102")
    assert restore_credentials("rtsp://other/stream", url) == "rtsp://other/stream"
    with pytest.raises(ValueError):
        restore_credentials("rtsp://***@cam/1", "rtsp://cam/1")
    assert mask_source(0) == "0"


def test_validate_source(tmp_path):
    videos = tmp_path / "videos"
    videos.mkdir()
    (videos / "demo.mp4").write_bytes(b"x")
    (tmp_path / "secret.mp4").write_bytes(b"x")
    assert validate_source("webcam", " 1 ", str(videos)) == (None, "1")
    assert validate_source("webcam", "cam", str(videos))[0]
    assert validate_source("rtsp", "rtsp://host/s", str(videos))[0] is None
    assert validate_source("rtsp", "file:///etc/passwd", str(videos))[0]
    assert validate_source("video", "demo.mp4", str(videos)) == (None, "demo.mp4")  # stored relative
    assert validate_source("video", str(videos / "demo.mp4"), str(videos)) == (None, "demo.mp4")
    from app.sources import resolve_video_source
    assert resolve_video_source("demo.mp4", str(videos)) == os.path.join(os.path.realpath(videos), "demo.mp4")
    assert "inside the video folder" in validate_source("video", "../secret.mp4", str(videos))[0]
    assert "inside the video folder" in validate_source("video", str(tmp_path / "secret.mp4"), str(videos))[0]
    assert "not found" in validate_source("video", "missing.mp4", str(videos))[0]
    assert "Unsupported" in validate_source("video", "demo.exe", str(videos))[0]


# --- camera API ----------------------------------------------------------------------------

def test_camera_api_masks_credentials_and_keeps_them_on_edit(api):
    client, factory = api
    body = {"name": "Gate CCTV", "source": "rtsp://admin:s3cret@10.0.0.9/ch1", "source_type": "rtsp",
            "camera_type": "IN"}
    created = client.post("/cameras/", json=body).json()
    assert created["source"] == "rtsp://***@10.0.0.9/ch1"
    assert all("s3cret" not in str(c) for c in client.get("/cameras/").json())

    edited = client.put(f"/cameras/{created['id']}", json={**body, "source": "rtsp://***@10.0.0.9/ch2",
                                                           "name": "Gate CCTV 2"})
    assert edited.status_code == 200 and edited.json()["source"] == "rtsp://***@10.0.0.9/ch2"
    db = factory()
    assert db.get(models.Camera, created["id"]).source == "rtsp://admin:s3cret@10.0.0.9/ch2"
    db.close()


def test_camera_api_validates_sources(api, tmp_path, monkeypatch):
    client, _ = api
    monkeypatch.setattr(settings, "VIDEO_FILES_DIR", str(tmp_path))
    (tmp_path / "lecture.mp4").write_bytes(b"x")
    base = {"name": "C", "camera_type": "IN"}
    assert client.post("/cameras/", json={**base, "source": "front door", "source_type": "webcam"}).status_code == 400
    assert client.post("/cameras/", json={**base, "source": "C:/Windows/win.ini", "source_type": "video"}).status_code == 400
    ok = client.post("/cameras/", json={**base, "source": "lecture.mp4", "source_type": "video"})
    assert ok.status_code == 200 and ok.json()["source"] == "lecture.mp4"
    assert client.post("/cameras/", json={**base, "source": "0", "source_type": "webcam", "classroom_id": 99}).status_code == 400


def test_connection_test_needs_explicit_confirmation_for_webcams(api, monkeypatch):
    client, factory = api
    db = factory()
    camera_id = add_camera(db).id  # webcam "0"
    db.close()
    probes = []
    monkeypatch.setattr(cameras_router, "probe_source",
                        lambda source, w, h: probes.append(source) or (True, "Opened and read a frame", [48, 64, 3]))
    r = client.post(f"/cameras/{camera_id}/test")
    assert r.status_code == 400 and "confirm" in r.json()["detail"] and probes == []
    r = client.post(f"/cameras/{camera_id}/test?confirm_device=true")
    assert r.status_code == 200 and r.json()["ok"] is True and probes == ["0"]


def test_start_refuses_missing_model_and_shared_device(api, monkeypatch, tmp_path):
    client, factory = api
    from app.routers import video
    db = factory()
    first, second = add_camera(db, "A"), add_camera(db, "B")  # both webcam "0"
    first_id, second_id = first.id, second.id
    db.close()

    monkeypatch.setattr(settings, "YOLO_MODEL_PATH", str(tmp_path / "missing.pt"))
    r = client.post(f"/video/{first_id}/start")
    assert r.status_code == 503 and "never downloads" in r.json()["detail"]
    assert first_id not in video.active_sessions

    # Another camera already processing device 0 -> the second record must not open it
    video.active_sessions[first_id] = {"source": "0", "starting": True}
    try:
        r = client.post(f"/video/{second_id}/start")
        assert r.status_code == 409 and "same source" in r.json()["detail"]
    finally:
        video.active_sessions.pop(first_id, None)
