# Known limitations

## Needs hardware, people or decisions (blocked, not software defects)

| Item | Why it is open | What is needed |
|---|---|---|
| Real CCTV / RTSP cameras | No campus camera has been connected; only fakes and placeholder files in tests | Camera URLs/credentials on the campus network, mounted at the classroom doors; then use **Test** and the demo runbook |
| Recognition accuracy (Phase I ≥ 50 %) | No labelled session with real enrolled people exists | Consenting participants (40–60 students + 3–5 visitors), a recorded or observed session, ground truth; run `scripts/evaluate_accuracy.py` |
| Threshold calibration | Defaults (0.6 / 0.45 / score 70) are reference values, not validated for these cameras | One calibration session separate from the evaluation session |
| Database upgrade of `data/attendai.db` | Changing the real database needs the owner's approval | Back up and run `alembic upgrade head` (verified on a copy) |
| First administrator | No account is admin after the upgrade, by design | `scripts/manage_users.py set-role <user> admin` |
| Ultralytics AGPL-3.0 | Licence obligations for deployment | Institutional decision: comply, buy a licence, or replace the detector |
| Scale to 500 students / 5 rooms | Not measured; CPU-only machine is slow at face encoding | Benchmark on the target hardware with real footage; likely GPU or AVX-enabled dlib |

## Recognition and tracking

- **Face-only identification.** People are identified only when a face is visible and large enough
  (≈ 60+ px). Backs of heads, masks, heavy occlusion, strong back-light or caps pulled low prevent recognition.
  A person who is never recognised at the door has no event (shown as *no entry/no exit recorded*).
- **Group movement** works when faces are visible, but a crowded doorway hides faces. The per-frame cost grows with
  the number of faces (~0.6 s per face on the development CPU), so events in groups are recorded later.
- **No liveness detection.** A photo or screen showing an enrolled face can be recognised as that person.
- **Look-alikes.** Two people closer than the matching tolerance can be confused. AMBIGUOUS classification only
  helps when both are enrolled and the margin is configured.
- **Appearance change** (beard, glasses, haircut) reduces similarity; enroll several photos and re-enroll when needed.
- **DeepSORT tracks** are per camera and used for display/robustness; identity across cameras comes from faces only.

## Unregistered occupancy

- In memory only: resets to 0 when the backend restarts; the dashboard shows since when it has been counting.
- Matching a visitor at the OUT camera to the same visitor at the IN camera uses face distance; a different camera
  angle or lighting can make the same person look different (they stay "inside") or two similar people look the
  same (merged). A visitor who leaves without their face being seen stays counted until midnight.
- Visitors are not attributed to classrooms.
- When an enrolled student is first counted as unregistered (e.g. before enrollment) and later recognised, at most
  the single nearest unregistered occupant within 0.45 is removed.

## Attendance logic

- A student's class is a single `class_name`; multiple sections per student need several records or a future
  membership table.
- Periods are weekly recurring; one-off extra classes need a period added and later deactivated.
- *Unverified* depends on camera-run records; runs that ended because the server crashed end at their last
  heartbeat (≤ 30 s accuracy).
- Times are server-local; the server, cameras and users must share one time zone.
- Daily "present" on the dashboard counts any entry that day; per-period status is on the Reports page.

## Platform

- One backend process (one Uvicorn worker). Camera state and occupancy are in memory, so several workers would
  each have their own and disagree.
- SQLite: fine for one process; heavy concurrent writing from many processes would need PostgreSQL.
- No TLS in the app; tokens are kept in browser `localStorage` (see [SECURITY_PRIVACY.md](SECURITY_PRIVACY.md)).
- Light theme only.
- The folder still contains legacy scripts and summary files from earlier iterations (`backend\*.py`,
  `backend\*.bat`, root `*.md` in the parent folder) and an older duplicate `frontend\` in the parent folder;
  they are not used by the application and were left untouched.
