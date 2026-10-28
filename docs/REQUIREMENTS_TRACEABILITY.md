# Requirements traceability

Source: the requirement list in the project brief (Phase I qualification, product scope, constraints). No original
SRM/Balaji e-mails or requirement documents were found in the repository, so the requirements are taken from the
brief as stated; nothing here claims to quote them.

Status: **PASS** = implemented and verified by the evidence named · **PARTIAL** = implemented, part of the
requirement unverified or missing · **BLOCKED** = needs hardware, people or a decision · **FAIL** = not met.
Automated tests use synthetic encodings or stubs unless noted; they never prove real-world recognition.

## A. Phase I demonstration

| # | Requirement | Implementation | Verification | Status | Remaining |
|---|---|---|---|---|---|
| A1 | 40–60 registered students in one class | Students (+ staff), roster CSV import, face enrollment with quality checks | `test_roster_import.py`, `test_enrollment_quality.py`; UI import tested; 5000-row limit | PASS (software) | Real roster and photos to be enrolled with consent |
| A2 | Two live CCTV feeds, IN and OUT | Camera records with IN/OUT role, webcam/RTSP/file, reconnect, health | `test_camera_reliability.py`, `test_video_pipeline.py` (fake captures) | **BLOCKED — HARDWARE** | Connect the campus cameras; run **Test** and the runbook |
| A3 | Face-based identification of registered students | HOG + dlib encodings, KNOWN/UNKNOWN/AMBIGUOUS classifier, stability rules | `test_face_matching.py`, `test_recognition_integration.py` (synthetic encodings) | PARTIAL | Accuracy unmeasured — **BLOCKED — VALIDATION REQUIRED** |
| A4 | Accurate entry and exit timestamps | Wall-clock decision times, DB timestamps (UTC), local display, inclusive date ranges | `test_attendance_engine.py`, `test_day_boundaries.py`, `test_integrity_and_timestamps.py` | PASS (software) | Real latency door-crossing → record not measured |
| A5 | Live view of identified people and events | Live Monitoring: annotated frames, recognition log (all cameras), events, health | Frontend `LiveMonitoringPage.test.js`; manual E2E with synthetic frames | PARTIAL | With real faces untested |
| A6 | Occupancy comparable with physical headcount | Dashboard "Inside now" (stored events) + live engine count; per classroom | `test_dashboard_and_recent.py`, `test_end_to_end_mocked.py` | PARTIAL | Comparison with a real headcount: evaluation protocol (occupancy log) |
| A7 | 3–5 unregistered people; dashboard count inside | UnknownOccupancyTracker integrated into every camera; dashboard, live page, alerts | `test_unknown_occupancy.py` (67), `test_recognition_integration.py`, `test_end_to_end_mocked.py` | PARTIAL | Real cross-camera matching accuracy — **BLOCKED — VALIDATION REQUIRED** |
| A8 | Simultaneous group entry and exit | Multiple faces per frame; shared thread-safe engine/tracker; adaptive frame gap for slow frames | `test_group_of_visitors_and_students_enter_together`, `test_slow_frames_do_not_prevent_confirmation`, concurrency tests | PARTIAL | Real crowded-door performance untested; ~0.6 s per face on CPU |
| A9 | Unknown faces, occlusion, poor lighting, false positives, appearance change | UNKNOWN vs AMBIGUOUS separation, stability frames, enrollment quality gates, multiple photos, tracking through face loss, manual corrections | Unit tests above | PARTIAL | Robustness unmeasured; no liveness; see LIMITATIONS |
| A10 | Live demo over Teams with the running application | [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md); honest feed labels; fallback without claiming live CCTV | Manual E2E walk-through on scratch data | PARTIAL | Rehearsal with real cameras and people |
| A11 | ≥ 50 % accuracy, measured honestly | `scripts/evaluate_accuracy.py`, protocol in TESTING_AND_EVALUATION.md | `test_scripts.py` (scoring logic only) | **BLOCKED — VALIDATION REQUIRED** | Labelled session with consenting participants |

## B. Product scope

| # | Requirement | Implementation | Verification | Status | Remaining |
|---|---|---|---|---|---|
| B1 | Students **and staff** | `person_type`; staff recognised and logged, excluded from rosters/class stats | `test_period_attendance.py` (staff excluded), `test_roster_import.py` | PASS | |
| B2 | Entry/exit records and time inside | Events + presence sessions (pairs, durations, unpaired flagged) | `test_sessions_pair_entries_and_exits_and_flag_unpaired` | PASS | |
| B3 | Attendance per scheduled class period | Classrooms, weekly periods, holidays, period status | `test_period_attendance.py` (20+ tests) | PASS (software) | Real timetable to be entered |
| B4 | Deviations, exceptions, alerts with reasons | Late, absent, left early, out of class, left & returned, did not return; dashboard alerts with basis | same + `test_live_endpoint_and_dashboard_report_the_shared_unknown_count` | PASS | |
| B5 | Student leaves at a break and does not return | `DID_NOT_RETURN_AFTER_BREAK` | `test_did_not_return_after_break` | PASS | |
| B6 | Caps, shadows, partial coverage, beard changes | Multiple photos per person, quality gates, ambiguity margin | – | PARTIAL | Needs evaluation scenarios |
| B7 | Occupancy analytics | Known/unregistered inside, per classroom, entries/exits today | Dashboard tests | PASS | Unregistered history not persisted (privacy choice) |
| B8 | Daily and monthly analytics | `/timetable/daily`, `/timetable/monthly`, 7-day trend; CSV/PDF | `test_daily_and_monthly_summaries_use_the_same_statuses`, export tests | PASS | |
| B9 | Multiple people moving simultaneously | See A8 | | PARTIAL | |
| B10 | Multiple classrooms and cameras | Classroom model, camera assignment, per-room coverage and occupancy; one worker thread per camera | `test_camera_of_another_classroom_does_not_count_as_coverage` | PASS (software) | Multi-room hardware untested |
| B11 | Path to ~500 students / 5 rooms | Benchmark script, measurements, scaling notes (ARCHITECTURE.md) | Benchmark run (CPU) | PARTIAL | Measure on target hardware; GPU / AVX dlib likely needed |
| B12 | CCTV and other sources | Webcam, RTSP/HTTP stream, video file; credentials masked | Source validation tests | PARTIAL | RTSP unproven with real cameras |
| B13 | Manual corrections with audit trail | Manual events, overrides, audit log | `test_manual_event_records_operator_reason_and_audit`, override test | PASS | |
| B14 | Readable, documented code for SRM IT handover | Docs set, HANDOVER.md, docstrings | Review | PASS | |
| B15 | Reports export | CSV (formula-safe) and PDF | Export tests | PASS | |

## C. Security, privacy, deployment

| # | Requirement | Implementation | Verification | Status | Remaining |
|---|---|---|---|---|---|
| C1 | No commercial attendance software; own logic | All attendance logic in this repo | Code review | PASS | |
| C2 | Fully local, no cloud at runtime | Local models, `YOLO_OFFLINE`, no external frontend resources | Code/config review, offline flag check | PASS | |
| C3 | No face data / footage / records sent out | No outbound calls in the app; photos never stored | Review | PASS | |
| C4 | No automatic model downloads; clear error | `check_model_file`, `ModelUnavailableError`, `/health` model check | `test_missing_model_is_a_setup_error_not_a_download`, `test_start_refuses_missing_model_and_shared_device` | PASS | |
| C5 | Licence review (Ultralytics) | SECURITY_PRIVACY.md | Package metadata checked | PARTIAL | AGPL decision by the institution |
| C6 | Never fabricate identities, feeds, events, results | Synthetic frames labelled everywhere; mocked tests labelled; no accuracy claimed | `test_feed_kinds_label_sources_honestly`, UI tests | PASS | |
| C7 | Protect credentials, camera URLs, PII | Masking in API/logs/audit, role checks, no-store headers, git-ignored data | `test_camera_api_masks_credentials…`, `test_audit_log_never_stores_stream_credentials` | PASS | TLS and token storage: see residual risks |
| C8 | Server-side authorization; registration not admin | Roles, `require_role`, viewer on sign-up | `test_permissions.py` | PASS | Promote the first admin |
| C9 | Liveness / spoofing | Not implemented; documented | – | FAIL (documented gap) | Needs a liveness method and evaluation |
| C10 | Deployment documentation | SETUP_WINDOWS.md, CONFIGURATION.md, OPERATIONS.md | Review | PASS | |
