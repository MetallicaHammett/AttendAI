"""
RTSP camera integration without hardware: a fake RTSP server on 127.0.0.1 with real Digest
authentication stands in for the NVR. Proves the failure classification, the single login
attempt per check, credential masking (passwords containing "@"), the Test and Start paths,
and independent feeds. It does NOT prove that the real SRM NVR streams work.
"""
import hashlib
import re
import socket
import threading
import time
from urllib.parse import quote, urlsplit

import numpy as np
import pytest

from app import models, rtsp_check, services
from app.routers import cameras as cameras_router
from app.sources import mask_source, restore_credentials

USER, PASSWORD = "viewer", "Ab@cd:1234/x"   # contains @ : / - must be percent-encoded in a URL
GOOD_PATH = "/Streaming/Channels/101"


class FakeRtspServer:
    """Minimal RTSP server: 401 Digest challenge, then 200 / 401 / 403 / 404 per request"""

    def __init__(self, paths=(GOOD_PATH,), forbidden=False, silent=False):
        self.paths, self.forbidden, self.silent = set(paths), forbidden, silent
        self.auth_attempts = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def url(self, path=GOOD_PATH, user=USER, password=PASSWORD, encode=True):
        if user is None:
            return f"rtsp://127.0.0.1:{self.port}{path}"
        q = (lambda s: quote(s, safe="")) if encode else (lambda s: s)
        return f"rtsp://{q(user)}:{q(password)}@127.0.0.1:{self.port}{path}"

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        conn.settimeout(2)
        buf = b""
        try:
            while not self._stop.is_set():
                while b"\r\n\r\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                head, buf = buf.split(b"\r\n\r\n", 1)
                lines = head.decode().split("\r\n")
                method, uri, _ = lines[0].split(" ")
                headers = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
                if self.silent:
                    time.sleep(3)
                    return
                cseq = headers.get("cseq", "1")
                auth = headers.get("authorization")
                if not auth:
                    conn.sendall((f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\nWWW-Authenticate: Digest '
                                  f'realm="testrealm", nonce="n0nce"\r\n\r\n').encode())
                    continue
                self.auth_attempts += 1
                p = dict(re.findall(r'(\w+)="([^"]*)"', auth))
                ha1 = hashlib.md5(f"{USER}:testrealm:{PASSWORD}".encode()).hexdigest()
                ha2 = hashlib.md5(f"{method}:{p.get('uri')}".encode()).hexdigest()
                good = (p.get("username") == USER
                        and p.get("response") == hashlib.md5(f"{ha1}:n0nce:{ha2}".encode()).hexdigest())
                path = urlsplit(uri).path
                status = ("401 Unauthorized" if not good else "404 Not Found" if path not in self.paths
                          else "403 Forbidden" if self.forbidden else "200 OK")
                conn.sendall(f"RTSP/1.0 {status}\r\nCSeq: {cseq}\r\nContent-Length: 0\r\n\r\n".encode())
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self._stop.set()
        self.sock.close()


@pytest.fixture
def server():
    srv = FakeRtspServer()
    yield srv
    srv.close()


# --- credential masking ----------------------------------------------------------------

@pytest.mark.parametrize("password", ["Ab@cd12345", "p@ss@word", "simple"])
def test_masking_hides_the_whole_password_even_with_at_signs(password):
    url = f"rtsp://admin:{password}@192.0.2.5:554/Streaming/Channels/101"
    masked = mask_source(url)
    assert masked == "rtsp://***@192.0.2.5:554/Streaming/Channels/101"
    assert all(part not in masked for part in password.split("@") if len(part) > 2)
    assert restore_credentials(masked, url) == url  # editing without retyping keeps the stored login


# --- classification ---------------------------------------------------------------------

def test_valid_login_and_path_is_ok_with_encoded_special_characters(server):
    result = rtsp_check.check(server.url())
    assert (result.ok, result.category) == (True, "ok") and server.auth_attempts == 1


def test_wrong_password_is_reported_once_without_leaking_it(server):
    result = rtsp_check.check(server.url(password="Wrong@pass99"))
    assert (result.ok, result.category, result.status) == (False, "auth_failed", 401)
    assert server.auth_attempts == 1                       # exactly one login attempt per check
    assert "Wrong" not in result.detail and "pass99" not in result.detail and USER not in result.detail
    assert "lock out" in result.detail


def test_missing_login_is_reported_without_any_login_attempt(server):
    result = rtsp_check.check(server.url(user=None))
    assert result.category == "auth_required" and server.auth_attempts == 0


def test_wrong_channel_path_is_reported(server):
    result = rtsp_check.check(server.url(path="/Streaming/Channels/3301"))
    assert result.category == "not_found" and "/Streaming/Channels/3301" in result.detail and "101/102" in result.detail


def test_no_permission_is_reported():
    srv = FakeRtspServer(forbidden=True)
    try:
        assert rtsp_check.check(srv.url()).category == "forbidden"
    finally:
        srv.close()


def test_unreachable_and_silent_devices_are_reported():
    with socket.socket() as s:            # a port with nothing listening
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert rtsp_check.check(f"rtsp://127.0.0.1:{free}/Streaming/Channels/101", timeout=1).category == "unreachable"
    silent = FakeRtspServer(silent=True)
    try:
        started = time.perf_counter()
        assert rtsp_check.check(silent.url(), timeout=0.5).category == "timeout"
        assert time.perf_counter() - started < 3
    finally:
        silent.close()


@pytest.mark.parametrize("url", ["rtsp://", "http://cam/stream", "not a url", ""])
def test_invalid_urls_are_rejected_without_network(url):
    assert rtsp_check.check(url).category == "bad_url"


# --- the camera Test action --------------------------------------------------------------

class OneFrameCapture:
    instances = []

    def __init__(self, *args, opens=True):
        self.args, self.opens, self.released = args, opens, False
        OneFrameCapture.instances.append(self)

    def isOpened(self):
        return self.opens

    def read(self):
        return True, np.zeros((720, 1280, 3), np.uint8)

    def release(self):
        self.released = True


def test_test_action_reports_the_login_problem_and_never_opens_opencv(server, monkeypatch):
    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a: pytest.fail("OpenCV must not be called"))
    ok, detail, shape = cameras_router.probe_source(server.url(password="nope"), 1280, 720)
    assert not ok and "rejected the username or password" in detail and shape is None


def test_test_action_reads_one_frame_and_releases_the_capture(server, monkeypatch):
    import cv2
    OneFrameCapture.instances = []
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a: OneFrameCapture(*a))
    ok, detail, shape = cameras_router.probe_source(server.url(), 1280, 720)
    assert ok and shape == [720, 1280, 3] and OneFrameCapture.instances[0].released


def test_test_action_explains_a_decoder_failure_after_a_good_login(server, monkeypatch):
    import cv2
    OneFrameCapture.instances = []
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a: OneFrameCapture(*a, opens=False))
    ok, detail, _ = cameras_router.probe_source(server.url(), 1280, 720)
    assert not ok and "could not be decoded" in detail and OneFrameCapture.instances[0].released


# --- Start ------------------------------------------------------------------------------

def test_start_reports_rejected_login_without_opening_or_recording_anything(api, server, monkeypatch):
    import cv2
    from app.routers import video
    client, factory = api
    db = factory()
    cam = models.Camera(name="B-308 IN", source=server.url(password="Wrong@pass99"), source_type="rtsp",
                        camera_type="IN", width=1280, height=720, fps=15)
    db.add(cam)
    db.commit()
    cam_id = cam.id
    db.close()
    monkeypatch.setattr(services.PersonDetectionService, "check_model_file", lambda self: None)
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a: pytest.fail("OpenCV must not be called"))
    r = client.post(f"/video/{cam_id}/start")
    assert r.status_code == 503 and "rejected the username or password" in r.json()["detail"]
    assert "Wrong" not in r.text and "pass99" not in r.text
    assert cam_id not in video.active_sessions          # slot released; another Start can run
    db = factory()
    assert db.query(models.CameraRun).count() == 0     # no coverage claimed for a camera that never ran
    db.close()
    assert "pass99" not in client.get("/cameras/").text and "pass99" not in client.get("/users/audit-log").text


# --- independent feeds ------------------------------------------------------------------

class Feed:
    def __init__(self, frames):
        self.left, self.released = frames, False

    def isOpened(self):
        return True

    def set(self, *a):
        return True

    def read(self):
        time.sleep(0.002)
        if self.left is not None:
            if self.left <= 0:
                return False, None
            self.left -= 1
        return True, np.zeros((48, 64, 3), np.uint8)

    def release(self):
        self.released = True


def test_one_feed_failing_does_not_stop_the_other(monkeypatch):
    feeds = {"rtsp://10.0.0.5/b308": Feed(frames=2), "rtsp://10.0.0.2/c101": Feed(frames=None)}
    monkeypatch.setattr(services.cv2, "VideoCapture", lambda src, *a: feeds[src])
    b308, c101 = (services.VideoProcessingService(video_source=src, source_type="rtsp", frame_width=64,
                                                  frame_height=48, camera_id=i, latest_frame_only=False)
                  for i, src in enumerate(feeds, 1))
    for svc in (b308, c101):
        svc._process_frame = lambda frame, ts, rec: (frame, [])
        svc.start()
    for _ in range(15):
        b308.process_frame(None)
        assert c101.process_frame(None)[0] is not None   # C101 keeps delivering frames
    assert b308.get_stats()["state"] == "disconnected" and c101.get_stats()["state"] == "running"
    for svc in (b308, c101):
        svc.stop()
