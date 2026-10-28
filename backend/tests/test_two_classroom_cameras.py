"""
Two classroom cameras at once (B-308 and BW102 style): concurrent sessions with separate
captures, attendance state kept per classroom, coverage that follows real frame delivery
(outages and reconnects), no duplicate events, and outages / partial coverage / other
classrooms' sightings never turning into absences. Fakes and temporary databases only:
no camera, stream or file is opened.
"""
import threading
import time
from datetime import date, datetime, time as dtime, timedelta

import numpy as np
import pytest

from app import attendance_logic, models, services
from app.config import settings
from app.routers import video
from helpers import add_camera, add_event, add_student, utc_naive

DAY = date.today() - timedelta(days=date.today().weekday() + 14)  # a past Monday


def at(hh, mm, day=DAY):
    return utc_naive(day, dtime(hh, mm))


def local_ts(hh, mm, day=None):
    day = day or date.today()
    return datetime.combine(day, dtime(hh, mm)).timestamp()


# --- attendance engine: one state per classroom ---------------------------------------

def confirm(engine, student, t0, camera_id, zone, persist=None, frames=5):
    return [engine.process_recognition(student, 90.0, t0 + i * 0.2, "IN", camera_id=camera_id, zone=zone,
                                       persist=persist) for i in range(frames)][-1]


def test_entry_in_one_classroom_does_not_block_entry_in_another():
    engine = services.AttendanceEngine(confidence_threshold=60, min_consecutive_frames=5)
    first = confirm(engine, 7, local_ts(9, 0), camera_id=1, zone="B-308")
    assert first.event_type == "ENTRY" and first.should_record
    # Same classroom again later: still inside there, no second ENTRY
    again = confirm(engine, 7, local_ts(9, 30), camera_id=1, zone="B-308")
    assert again.event_type == "NO_CHANGE" and not again.should_record
    # The other classroom's camera records its own ENTRY
    other = confirm(engine, 7, local_ts(10, 5), camera_id=2, zone="BW102")
    assert other.event_type == "ENTRY" and other.should_record
    assert engine.inside_count(local_ts(10, 6)) == 1  # one person, not two


def test_cameras_without_a_classroom_keep_the_building_wide_behaviour():
    engine = services.AttendanceEngine(confidence_threshold=60, min_consecutive_frames=5)
    assert confirm(engine, 7, local_ts(9, 0), camera_id=1, zone=None).event_type == "ENTRY"
    assert confirm(engine, 7, local_ts(10, 0), camera_id=2, zone=None).event_type == "NO_CHANGE"
    assert 7 in engine.student_states  # same keys as before zones existed


def test_restart_restores_per_classroom_state_so_no_duplicate_entry():
    engine = services.AttendanceEngine(confidence_threshold=60, min_consecutive_frames=5)
    engine.seed_from_events([(7, "ENTRY", local_ts(9, 0), "B-308")])
    assert confirm(engine, 7, local_ts(9, 40), camera_id=1, zone="B-308").event_type == "NO_CHANGE"
    assert confirm(engine, 7, local_ts(10, 5), camera_id=2, zone="BW102").event_type == "ENTRY"


def test_a_reconnect_gap_never_duplicates_an_entry():
    """Sightings stop (stream drops), the gap resets stability, sightings resume: the
    student is still inside that classroom, so nothing new is recorded"""
    engine = services.AttendanceEngine(confidence_threshold=60, min_consecutive_frames=5, entry_cooldown=300)
    recorded = []
    confirm(engine, 7, local_ts(9, 0), camera_id=1, zone=1, persist=recorded.append)
    for minutes in (1, 3, 8, 20):  # resumes after outages of various lengths
        confirm(engine, 7, local_ts(9, minutes), camera_id=1, zone=1, persist=recorded.append, frames=10)
    assert [d.event_type for d in recorded] == ["ENTRY"]


# --- two sessions at once, separate captures ------------------------------------------

class FakeStream:
    """A live source that yields numbered frames; marks which source produced them"""
    def __init__(self, source):
        self.source, self.n, self.released = source, 0, False
        self.lock = threading.Lock()

    def isOpened(self):
        return True

    def set(self, *args):
        return True

    def read(self):
        time.sleep(0.01)
        with self.lock:
            self.n += 1
            frame = np.full((48, 64, 3), 1 if "192.0.2.5" in self.source else 2, np.uint8)
        return True, frame

    def release(self):
        self.released = True


def test_two_stream_sessions_run_concurrently_with_their_own_capture(monkeypatch):
    opened = {}

    def fake_capture(source, *args):
        opened[source] = FakeStream(source)
        return opened[source]
    monkeypatch.setattr(services.cv2, "VideoCapture", fake_capture)

    seen = {1: [], 2: []}
    services_ = []
    for camera_id, url in ((1, "rtsp://192.0.2.5:554/Streaming/Channels/101"),
                           (2, "rtsp://192.0.2.2:554/Streaming/Channels/101")):
        svc = services.VideoProcessingService(video_source=url, source_type="rtsp", camera_id=camera_id, zone=camera_id,
                                              latest_frame_only=True, frame_width=64, frame_height=48)
        svc._process_frame = (lambda frame, ts, rec, cid=camera_id: (seen[cid].append(int(frame[0, 0, 0])), (frame, []))[1])
        svc.start()
        services_.append(svc)
    try:
        threads = [threading.Thread(target=lambda s=s: [s.process_frame(None) for _ in range(15)]) for s in services_]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert len(opened) == 2  # one capture object per source, never shared
        assert services_[0]._reader is not None and services_[1]._reader is not None
        assert services_[0]._reader._cap is not services_[1]._reader._cap
        # Each session only ever processed frames from its own source
        assert seen[1] and set(seen[1]) == {1}
        assert seen[2] and set(seen[2]) == {2}
    finally:
        for s in services_:
            s.stop()
    deadline = time.time() + 2
    while time.time() < deadline and not all(s.released for s in opened.values()):
        time.sleep(0.05)
    assert all(s.released for s in opened.values())  # both captures released on stop


# --- coverage follows real frame delivery ---------------------------------------------

class FakeService:
    def __init__(self):
        self.state, self.last_frame_at, self.started_at = "running", None, time.time()
        self.feed_kind, self.use_test_source = "live", False


@pytest.fixture
def camera_run(api):
    _, factory = api
    db = factory()
    room = models.Classroom(name="B-308")
    db.add(room)
    db.commit()
    cam = add_camera(db, "B-308 CCTV", "IN")
    cam.classroom_id = room.id
    run = models.CameraRun(camera_id=cam.id, started_at=datetime.utcnow(), feed_kind="live")
    db.add(run)
    db.commit()
    ids = {"camera": cam.id, "run": run.id, "room": room.id}
    db.close()
    return factory, ids


def runs(factory, camera_id):
    db = factory()
    out = [(r.id, r.stopped_at, r.stop_reason, r.last_heartbeat_at)
           for r in db.query(models.CameraRun).filter_by(camera_id=camera_id).order_by(models.CameraRun.id)]
    db.close()
    return out


def test_outage_closes_coverage_and_reconnect_opens_a_new_record(camera_run):
    factory, ids = camera_run
    db = factory()
    session = {"run_id": ids["run"]}
    svc = FakeService()
    now = time.time()
    svc.last_frame_at = now
    video.track_coverage(db, session, svc, ids["camera"], now=now)        # frames arriving: heartbeat
    assert runs(factory, ids["camera"])[0][3] is not None
    svc.state = "reconnecting"                                             # stream dropped
    video.track_coverage(db, session, svc, ids["camera"], now=now + 20)
    first = runs(factory, ids["camera"])[0]
    assert first[1] is not None and first[2] == "feed lost (reconnecting)" and session["run_id"] is None
    video.track_coverage(db, session, svc, ids["camera"], now=now + 40)    # still down: nothing new
    assert len(runs(factory, ids["camera"])) == 1
    svc.state, svc.last_frame_at = "running", now + 60                      # reconnected
    video.track_coverage(db, session, svc, ids["camera"], now=now + 60)
    all_runs = runs(factory, ids["camera"])
    assert len(all_runs) == 2 and all_runs[1][1] is None and session["run_id"] == all_runs[1][0]
    db.close()


def test_a_stalled_feed_that_is_still_running_also_closes_coverage(camera_run):
    factory, ids = camera_run
    db = factory()
    session, svc, now = {"run_id": ids["run"]}, FakeService(), time.time()
    svc.last_frame_at = now - settings.CAMERA_STALE_AFTER_SECONDS - 5  # no new frame for a while
    video.track_coverage(db, session, svc, ids["camera"], now=now)
    assert runs(factory, ids["camera"])[0][2] == "feed lost (running)"
    db.close()


def test_startup_is_given_time_before_coverage_is_closed(camera_run):
    factory, ids = camera_run
    db = factory()
    session, svc = {"run_id": ids["run"]}, FakeService()
    video.track_coverage(db, session, svc, ids["camera"], now=svc.started_at + 5)   # models loading
    assert runs(factory, ids["camera"])[0][1] is None
    video.track_coverage(db, session, svc, ids["camera"], now=svc.started_at + 60)  # never got a frame
    assert runs(factory, ids["camera"])[0][1] is not None
    db.close()


# --- period attendance: outages, partial coverage, other classrooms -------------------

@pytest.fixture
def two_rooms(api):
    """B-308 and BW102, one IN camera each; class CSE Cybersecurity (A1, A2) has Maths in
    BW102 on Monday 10:00-11:00"""
    client, factory = api
    db = factory()
    b308, bw102 = models.Classroom(name="B-308"), models.Classroom(name="BW102")
    db.add_all([b308, bw102])
    db.commit()
    cam_b308, cam_bw102 = add_camera(db, "B-308 CCTV", "IN"), add_camera(db, "BW102 CCTV", "IN")
    cam_b308.classroom_id, cam_bw102.classroom_id = b308.id, bw102.id
    ids = {"b308": cam_b308.id, "bw102": cam_bw102.id}
    for sid in ("A1", "A2"):
        s = add_student(db, sid)
        s.class_name = "CSE Cybersecurity"
        ids[sid] = s.id
    period = models.TimetablePeriod(classroom_id=bw102.id, class_name="CSE Cybersecurity", course_name="Maths",
                                    day_of_week=0, start_time=dtime(10, 0), end_time=dtime(11, 0), grace_minutes=10)
    period.created_at = at(8, 0, DAY - timedelta(days=30))
    db.add(period)
    db.commit()
    ids["period"] = period.id
    db.close()
    return client, factory, ids


def add_run(factory, camera_id, start, end, reason=None):
    db = factory()
    db.add(models.CameraRun(camera_id=camera_id, started_at=start, stopped_at=end, stop_reason=reason, feed_kind="live"))
    db.commit()
    db.close()


def statuses(client, period_id):
    r = client.get(f"/timetable/periods/{period_id}/attendance?date={DAY}")
    assert r.status_code == 200, r.text
    return {s["student_id"]: s["status"] for s in r.json()["students"]}, r.json()


def test_fully_covered_class_reports_present_and_absent(two_rooms):
    client, factory, ids = two_rooms
    add_run(factory, ids["bw102"], at(9, 50), at(11, 5))
    db = factory()
    add_event(db, ids["A1"], "ENTRY", at(10, 2), ids["bw102"])
    db.close()
    result, body = statuses(client, ids["period"])
    assert result == {"A1": "PRESENT", "A2": "ABSENT"} and body["camera_coverage"] is True


def test_a_stream_outage_during_class_never_produces_absent(two_rooms):
    client, factory, ids = two_rooms
    add_run(factory, ids["bw102"], at(9, 50), at(10, 20), reason="feed lost (reconnecting)")
    add_run(factory, ids["bw102"], at(10, 45), at(11, 5))          # 25 minutes without frames
    result, body = statuses(client, ids["period"])
    assert result == {"A1": "UNVERIFIED", "A2": "UNVERIFIED"} and body["camera_coverage"] is False


def test_a_short_gap_within_the_allowance_still_counts_as_covered(two_rooms):
    client, factory, ids = two_rooms
    add_run(factory, ids["bw102"], at(10, 2), at(10, 30))          # started 2 min late
    add_run(factory, ids["bw102"], at(10, 31), at(11, 0))          # 1 min reconnect
    assert statuses(client, ids["period"])[1]["camera_coverage"] is True


def test_an_open_run_whose_heartbeat_stopped_does_not_cover_the_class(two_rooms):
    client, factory, ids = two_rooms
    db = factory()
    db.add(models.CameraRun(camera_id=ids["bw102"], started_at=at(9, 50), stopped_at=None,
                            last_heartbeat_at=at(10, 10), feed_kind="live"))
    db.commit()
    db.close()
    result, _ = statuses(client, ids["period"])
    assert result["A2"] == "UNVERIFIED"


def test_being_seen_in_another_classroom_is_not_attendance_here(two_rooms):
    client, factory, ids = two_rooms
    add_run(factory, ids["bw102"], at(9, 50), at(11, 5))
    db = factory()
    add_event(db, ids["A2"], "ENTRY", at(9, 55), ids["b308"])     # in B-308, not BW102
    db.close()
    result, _ = statuses(client, ids["period"])
    assert result["A2"] == "ABSENT"
    # The professor's register shows it as a hint, never as presence
    reg = client.get(f"/staff/periods/{ids['period']}/register?date={DAY}").json()
    a2 = next(s for s in reg["students"] if s["student_id"] == "A2")
    assert a2["status"] == "UNVERIFIED" and a2["ai_status"] == "SEEN_ELSEWHERE"
    assert a2["evidence"]["camera"] == "B-308 CCTV"


def test_manual_corrections_still_count_in_a_classroom_with_cameras(two_rooms):
    client, factory, ids = two_rooms
    add_run(factory, ids["bw102"], at(9, 50), at(11, 5))
    db = factory()
    event = add_event(db, ids["A2"], "ENTRY", at(10, 3), None)
    event.source = "manual"
    db.commit()
    db.close()
    assert statuses(client, ids["period"])[0]["A2"] == "PRESENT"


def test_camera_start_passes_its_classroom_as_the_attendance_zone(api, monkeypatch):
    client, factory = api
    db = factory()
    room = models.Classroom(name="BW102")
    db.add(room)
    db.commit()
    cam = models.Camera(name="BW102 CCTV", source="rtsp://viewer:s3cret-pw@192.0.2.2:554/Streaming/Channels/101",
                        source_type="rtsp", camera_type="IN", classroom_id=room.id)
    db.add(cam)
    db.commit()
    cam_id, room_id = cam.id, room.id
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
    r = client.post(f"/video/{cam_id}/start")
    assert r.status_code == 503 and seen["zone"] == room_id
    assert "could not be opened" in r.text and "s3cret-pw" not in r.text and "viewer:" not in r.text
    db = factory()
    assert db.query(models.CameraRun).filter_by(camera_id=cam_id).count() == 0  # no coverage for a failed start
    db.close()
