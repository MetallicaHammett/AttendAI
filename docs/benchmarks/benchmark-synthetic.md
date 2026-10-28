# AttendAI benchmark (2026-10-20 10:14:51)

Machine: Windows-11-10.0.26200-SP0, 16 logical CPUs, RAM 16.3 GB, CUDA available: False
Input: synthetic frames (no people/faces: detection cost only, no recognition load), 640x480

## Face matching (per detected face)

| Identities | Encodings | mean ms | p95 ms |
|---|---|---|---|
| 40 | 120 | 0.06 | 0.08 |
| 60 | 180 | 0.09 | 0.15 |
| 500 | 1500 | 1.66 | 2.28 |
| 2000 | 6000 | 7.79 | 8.98 |

## Full pipeline per frame

| Cameras | Frames | FPS total | FPS/camera | mean ms | p95 ms | CPU % | RSS MB | errors |
|---|---|---|---|---|---|---|---|---|
| 1 | 290 | 19.31 | 19.31 | 41.21 | 48.06 | 786.7 | 448.7 | 0 |
| 2 | 456 | 30.34 | 15.17 | 60.29 | 69.97 | 1436.6 | 535.6 | 0 |
