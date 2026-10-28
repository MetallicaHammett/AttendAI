# Live demonstration runbook (Microsoft Teams)

Goal: show the running application with real behaviour, and label clearly anything that is not live.
Only enroll and film people who have consented. Do not run the demo against attendance data that matters
unless that is intended; a separate demo database is safer (set `DATABASE_URL` and upgrade it).

## The day before

1. Upgrade/back up the database ([SETUP_WINDOWS.md](SETUP_WINDOWS.md)); check `/health` shows `schema: up_to_date`
   and `person_detection_model: ok`.
2. Accounts: one **admin** for setup, one **operator** to present (fewer buttons to mis-click).
3. Classroom, IN and OUT cameras (assigned to it), periods for the demo time, roster imported, 2–4 photos per
   participant. Students page shows no "not enrolled" warning.
4. **Test** each camera (webcam asks to confirm the device). Start both on Live Monitoring and walk through the
   door once: an ENTRY should appear within a few seconds.
5. Record a short fallback video of a rehearsal (with consent), copy it into `backend\data\videos\`, and add it as a
   *Video file* camera. It will be labelled **Recorded video** everywhere.
6. Note the physical headcount procedure (someone counts people in the room at fixed times).

## Running order (≈ 20 minutes)

| Step | Screen | What to show / say |
|---|---|---|
| 1 | Terminal | Start backend (one worker) and frontend; open `/health` |
| 2 | Teams | Share the browser window, not the whole desktop (no other data visible) |
| 3 | Login → Dashboard | KPI definitions (hover ⓘ), cameras processing, alerts |
| 4 | Cameras | IN/OUT roles, classroom, live vs recorded labels, masked RTSP credentials |
| 5 | Live Monitoring | Start IN and OUT (confirmation names the device). Feed shows **Live feed** |
| 6 | Door | One student walks in → green box with name → ENTRY in the recognition log and Recent events |
| 7 | Door | Group of 3–5 students enters together → several ENTRY events (may take a few seconds on CPU) |
| 8 | Door | 3–5 unregistered volunteers enter → "Unregistered inside" rises; they leave via OUT → it falls |
| 9 | Room | Compare "Inside now" + "Unregistered inside" with the physical headcount, say the difference aloud |
| 10 | Door | Student leaves through OUT → EXIT; Attendance → Presence sessions shows time inside |
| 11 | Cameras | Disconnect: unplug the webcam / stop the stream → "Reconnecting" → plug back → frames resume |
| 12 | Reports | Period attendance: present/late/absent, an exception with its reason; override one with a reason; Users → audit log shows it |
| 13 | Reports | Daily summary → Export PDF / CSV |

## If something fails

- **A camera will not open**: show the error message, use the other camera or the recorded-video camera, and say
  that it is recorded footage ("Recorded video" chip). Never present it as live CCTV.
- **Recognition misses someone**: show the presence session "No entry recorded" and the audited manual
  correction; this is the designed recovery path.
- **Backend down**: the top bar shows "API offline"; restart the backend — attendance state is restored from the
  database, the unregistered count restarts from 0 (it is in memory, and the dashboard says so).

## What not to claim

- No accuracy percentage unless it was measured with `scripts/evaluate_accuracy.py` on a labelled session.
- No liveness/anti-spoofing.
- No 500-student scale claim without a benchmark on the target hardware.
