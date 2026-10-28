"""
Repeatable performance benchmark (no database writes, no cameras opened).

  .\\.venv\\Scripts\\python.exe scripts\\benchmark_pipeline.py --out ..\\docs\\benchmarks\\latest
  .\\.venv\\Scripts\\python.exe scripts\\benchmark_pipeline.py --video data\\videos\\sample.mp4 --cameras 2

Parts:
 1. Face matching cost vs number of enrolled identities (real matching code, synthetic
    128-d encodings, 3 encodings per identity).
 2. Full per-frame pipeline latency with the real local models (YOLOv8n person detection,
    face_recognition HOG, DeepSORT) on either a recorded video you supply (--video) or
    synthetic frames. Synthetic frames contain no people or faces, so they measure the
    fixed detection cost only - the report says which input was used.
 3. Several cameras processed concurrently in one process (threads), as the server does.

CPU/memory come from psutil when available. Results describe THIS machine only.
"""
import argparse
import json
import os
import platform
import statistics
import sys
import threading
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "benchmark-only-key-not-used-for-anything-0123456789")

import numpy as np  # noqa: E402

from app import services  # noqa: E402
from app.config import settings  # noqa: E402

try:
    import psutil
except ImportError:  # optional
    psutil = None


def pct(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q / 100 * len(ordered))) - 1))]


def summary_ms(values):
    return {"n": len(values), "mean": round(statistics.mean(values), 2), "p50": round(pct(values, 50), 2),
            "p95": round(pct(values, 95), 2), "max": round(max(values), 2)}


def bench_matching(sizes, probes=200):
    rng = np.random.default_rng(1)
    detector = services.FaceDetectionService()
    results = []
    for n in sizes:
        known = [rng.normal(0, 0.09, 128) for _ in range(n * 3)]
        ids = [i // 3 for i in range(n * 3)]
        queries = [rng.normal(0, 0.09, 128) for _ in range(probes)]
        times = []
        for q in queries:
            t = time.perf_counter()
            detector.classify_encoding(q, known, ids)
            times.append((time.perf_counter() - t) * 1000)
        results.append({"identities": n, "encodings": n * 3, "per_face_ms": summary_ms(times)})
    return results


class FileFrames:
    def __init__(self, path, width, height):
        import cv2
        self.cv2, self.cap, self.size = cv2, cv2.VideoCapture(path), (width, height)
        if not self.cap.isOpened():
            raise SystemExit(f"Cannot open video {path}")

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        return self.cv2.resize(frame, self.size)


class SyntheticFrames:
    def __init__(self, width, height):
        self.svc = services.VideoProcessingService(frame_width=width, frame_height=height)

    def read(self):
        self.svc.frame_count += 1
        return self.svc._generate_test_frame()


def make_service(width, height, identities, camera_id):
    rng = np.random.default_rng(camera_id)
    detector = services.FaceDetectionService()
    detector.load_known_faces([(i // 3, rng.normal(0, 0.09, 128)) for i in range(identities * 3)])
    svc = services.VideoProcessingService(
        video_source="benchmark", frame_width=width, frame_height=height, camera_id=camera_id,
        face_detector=detector, unknown_tracker=services.UnknownOccupancyTracker(),
        person_detector=services.PersonDetectionService(model_path=settings.YOLO_MODEL_PATH))
    svc.is_running = True
    return svc


def run_camera(svc, frames, duration, latencies, errors):
    deadline = time.time() + duration
    while time.time() < deadline:
        frame = frames.read()
        t = time.perf_counter()
        try:
            svc._process_frame(frame, time.time(), None)
        except Exception as e:  # counted, never hidden
            errors.append(f"{type(e).__name__}: {e}")
        latencies.append((time.perf_counter() - t) * 1000)


def bench_pipeline(args):
    services.PersonDetectionService(model_path=settings.YOLO_MODEL_PATH).check_model_file()
    results = []
    for cameras in sorted({1, args.cameras}):
        svcs = [make_service(args.width, args.height, args.identities, i + 1) for i in range(cameras)]
        sources = [FileFrames(args.video, args.width, args.height) if args.video else
                   SyntheticFrames(args.width, args.height) for _ in range(cameras)]
        warm = svcs[0]
        for _ in range(3):  # model loading / first-inference warm-up, not measured
            warm._process_frame(sources[0].read(), time.time(), None)
        for svc in svcs[1:]:
            svc._process_frame(sources[0].read(), time.time(), None)

        latencies = [[] for _ in range(cameras)]
        errors = []
        process = psutil.Process() if psutil else None
        if process:
            process.cpu_percent(None)
        threads = [threading.Thread(target=run_camera, args=(svcs[i], sources[i], args.duration, latencies[i], errors))
                   for i in range(cameras)]
        start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start
        all_ms = [v for per in latencies for v in per]
        results.append({
            "cameras": cameras,
            "frames_processed": len(all_ms),
            "duration_s": round(elapsed, 1),
            "throughput_fps_total": round(len(all_ms) / elapsed, 2),
            "throughput_fps_per_camera": round(len(all_ms) / elapsed / cameras, 2),
            "latency_ms": summary_ms(all_ms),
            "pipeline_errors": svcs[0].pipeline_error,
            "exceptions": len(errors),
            "cpu_percent_process": round(process.cpu_percent(None), 1) if process else None,
            "rss_mb": round(process.memory_info().rss / 1e6, 1) if process else None,
        })
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", help="recorded video to use instead of synthetic frames")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--cameras", type=int, default=2)
    parser.add_argument("--identities", type=int, default=60, help="enrolled identities for the pipeline run")
    parser.add_argument("--duration", type=float, default=20.0, help="seconds per pipeline run")
    parser.add_argument("--skip-pipeline", action="store_true")
    parser.add_argument("--out", help="write <out>.json and <out>.md")
    args = parser.parse_args(argv)

    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "machine": {"platform": platform.platform(), "processor": platform.processor(),
                    "logical_cpus": os.cpu_count(), "python": platform.python_version(),
                    "total_ram_gb": round(psutil.virtual_memory().total / 1e9, 1) if psutil else None},
        "input": f"recorded video {os.path.basename(args.video)}" if args.video else
                 "synthetic frames (no people/faces: detection cost only, no recognition load)",
        "frame_size": [args.width, args.height],
        "matching": bench_matching([40, 60, 500, 2000]),
    }
    try:
        import torch
        report["machine"]["cuda_available"] = bool(torch.cuda.is_available())
    except Exception:
        report["machine"]["cuda_available"] = None
    if not args.skip_pipeline:
        report["pipeline"] = bench_pipeline(args)

    lines = [f"# AttendAI benchmark ({report['generated']})", "",
             f"Machine: {report['machine']['platform']}, {report['machine']['logical_cpus']} logical CPUs, "
             f"RAM {report['machine']['total_ram_gb']} GB, CUDA available: {report['machine']['cuda_available']}",
             f"Input: {report['input']}, {args.width}x{args.height}", "",
             "## Face matching (per detected face)", "", "| Identities | Encodings | mean ms | p95 ms |", "|---|---|---|---|"]
    for m in report["matching"]:
        lines.append(f"| {m['identities']} | {m['encodings']} | {m['per_face_ms']['mean']} | {m['per_face_ms']['p95']} |")
    if "pipeline" in report:
        lines += ["", "## Full pipeline per frame", "",
                  "| Cameras | Frames | FPS total | FPS/camera | mean ms | p95 ms | CPU % | RSS MB | errors |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for p in report["pipeline"]:
            lines.append(f"| {p['cameras']} | {p['frames_processed']} | {p['throughput_fps_total']} | "
                         f"{p['throughput_fps_per_camera']} | {p['latency_ms']['mean']} | {p['latency_ms']['p95']} | "
                         f"{p['cpu_percent_process']} | {p['rss_mb']} | {p['exceptions']} |")
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out + ".json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        with open(args.out + ".md", "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
