"""
Measure real-world accuracy of AttendAI against human-labelled ground truth.

Nothing here generates or assumes accuracy: you record a session with consenting,
enrolled participants (plus unregistered volunteers), a human writes down what actually
happened, and this script compares that with what AttendAI recorded.

1. Create blank templates:
       .\\.venv\\Scripts\\python.exe scripts\\evaluate_accuracy.py --write-templates eval\\
2. During/after the session fill in eval\\ground_truth.csv (one row per real crossing):
       person,event_type,timestamp,notes
       21CSE001,ENTRY,2026-10-21T09:01:12,walked in with group of 4
       VISITOR-1,ENTRY,2026-10-21T09:02:30,unregistered volunteer, cap
   `person` is the institution ID of an enrolled person, or VISITOR-<n> for unregistered.
   Optionally fill eval\\occupancy_log.csv by reading the dashboard at fixed times:
       timestamp,actual_known,actual_unknown,shown_known,shown_unknown
3. Evaluate against the database (opened READ-ONLY) for the session window:
       .\\.venv\\Scripts\\python.exe scripts\\evaluate_accuracy.py --db data\\attendai.db ^
           --ground-truth eval\\ground_truth.csv --start 2026-10-21T08:55 --end 2026-10-21T10:00 ^
           [--occupancy-log eval\\occupancy_log.csv] [--tolerance 10] --out eval\\report
   or against an exported events CSV (--events-csv) with columns person,event_type,timestamp.

Metrics (each crossing of a person through a door is ONE case, never one per frame):
  matched          system event for the same person and type within ±tolerance seconds
  missed           labelled crossing of an enrolled person with no matching system event
  wrong_identity   system event whose person differs from the labelled crossing at that
                   time/type (a false acceptance of the wrong identity)
  spurious         system event with no labelled crossing nearby (includes visitors
                   recorded as students)
  identification_accuracy = matched / (enrolled crossings + wrong_identity + spurious)
  recall = matched / enrolled crossings;  precision = matched / system events
  timing: |system time - labelled time| for matched events (mean, median, p95, max)
  unknown occupancy: mean absolute error between actual and shown unregistered count
Report the conditions (lighting, angle, group size, occlusion) next to the numbers.
"""
import argparse
import csv
import json
import os
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class Crossing:
    person: str
    event_type: str
    at: datetime  # local naive
    notes: str = ""


def parse_time(text: str) -> datetime:
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def read_crossings(path: str) -> List[Crossing]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    crossings = []
    for line, row in enumerate(rows, start=2):
        event_type = (row.get("event_type") or "").strip().upper()
        if event_type not in ("ENTRY", "EXIT"):
            raise SystemExit(f"{path}:{line}: event_type must be ENTRY or EXIT")
        crossings.append(Crossing(row["person"].strip(), event_type, parse_time(row["timestamp"]),
                                  (row.get("notes") or "").strip()))
    return crossings


def _db_events(db_path: str, start: datetime, end: datetime):
    """(camera events, number of manual events) in the window, database opened read-only.
    Manual events (operator entries, admin edits) are corrections made by people, not camera
    recognitions: counting them would inflate the accuracy, so they are excluded and counted."""
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        has_source = "source" in {r[1] for r in con.execute("PRAGMA table_info(attendance_events)")}
        rows = con.execute(
            "SELECT s.student_id, e.event_type, e.timestamp, " + ("e.source" if has_source else "'camera'") + " "
            "FROM attendance_events e JOIN students s ON s.id = e.student_id")
        events, manual = [], 0
        for person, event_type, stamp, source in rows:
            local = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
            if start <= local <= end:
                if (source or "camera") != "camera":
                    manual += 1
                else:
                    events.append(Crossing(person, event_type, local))
        return events, manual
    finally:
        con.close()


def read_db_events(db_path: str, start: datetime, end: datetime) -> List[Crossing]:
    """Camera-recognised attendance events (manual events excluded), database opened read-only"""
    return _db_events(db_path, start, end)[0]


def evaluate(truth: List[Crossing], system: List[Crossing], tolerance: float) -> Dict:
    enrolled_truth = [c for c in truth if not c.person.upper().startswith("VISITOR")]
    visitors = [c for c in truth if c.person.upper().startswith("VISITOR")]
    unused = sorted(system, key=lambda e: e.at)
    matched, timing = [], []

    # 1. exact matches (same person + type, closest in time within tolerance)
    for crossing in sorted(enrolled_truth, key=lambda c: c.at):
        candidates = [e for e in unused if e.person == crossing.person and e.event_type == crossing.event_type
                      and abs((e.at - crossing.at).total_seconds()) <= tolerance]
        if candidates:
            best = min(candidates, key=lambda e: abs((e.at - crossing.at).total_seconds()))
            unused.remove(best)
            matched.append((crossing, best))
            timing.append(abs((best.at - crossing.at).total_seconds()))
    missed = [c for c in enrolled_truth if c not in [m[0] for m in matched]]

    # 2. a second system event for a crossing that was already matched = duplicate (spurious)
    duplicates = [e for e in unused if any(
        c.person == e.person and c.event_type == e.event_type and abs((e.at - c.at).total_seconds()) <= tolerance
        for c in truth)]
    for event in duplicates:
        unused.remove(event)

    # 3. remaining system events near a labelled crossing of someone else = wrong identity
    wrong_identity = []
    for event in list(unused):
        near = [c for c in truth if c.event_type == event.event_type and c.person != event.person
                and abs((event.at - c.at).total_seconds()) <= tolerance]
        if near:
            wrong_identity.append({"system_person": event.person, "actual_person": near[0].person,
                                   "event_type": event.event_type, "at": event.at.isoformat(timespec="seconds")})
            unused.remove(event)
    spurious = [{"person": e.person, "event_type": e.event_type, "at": e.at.isoformat(timespec="seconds"),
                 "reason": "duplicate" if e in duplicates else "no labelled crossing"}
                for e in sorted(duplicates + unused, key=lambda e: e.at)]

    denominator = len(enrolled_truth) + len(wrong_identity) + len(spurious)
    # Unregistered people: correct handling = no attendance event for their crossing
    visitors_recorded = sum(1 for w in wrong_identity if w["actual_person"].upper().startswith("VISITOR"))
    result = {
        "tolerance_seconds": tolerance,
        "labelled_crossings": len(truth),
        "enrolled_crossings": len(enrolled_truth),
        "visitor_crossings": len(visitors),
        "system_events": len(system),
        "matched": len(matched),
        "missed": len(missed),
        "wrong_identity": len(wrong_identity),
        "spurious": len(spurious),
        "false_negatives": len(missed),
        "false_positives": len(wrong_identity) + len(spurious),
        "visitors_recorded_as_students": visitors_recorded,
        "visitor_crossings_correctly_not_recorded": max(0, len(visitors) - visitors_recorded),
        "denominator": denominator,
        "accuracy_formula": "matched / (enrolled crossings + wrong identity + spurious)",
        "identification_accuracy": round(len(matched) / denominator * 100, 1) if denominator else None,
        "recall": round(len(matched) / len(enrolled_truth) * 100, 1) if enrolled_truth else None,
        "precision": round(len(matched) / len(system) * 100, 1) if system else None,
        "timing_seconds": {
            "mean": round(statistics.mean(timing), 2), "median": round(statistics.median(timing), 2),
            "p95": round(sorted(timing)[max(0, int(round(0.95 * len(timing))) - 1)], 2), "max": round(max(timing), 2),
        } if timing else None,
        "missed_details": [{"person": c.person, "event_type": c.event_type, "at": c.at.isoformat(timespec="seconds"),
                            "notes": c.notes} for c in missed],
        "wrong_identity_details": wrong_identity,
        "spurious_details": spurious,
    }
    return result


def evaluate_occupancy(path: str) -> Optional[Dict]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return None
    known = [abs(int(r["actual_known"]) - int(r["shown_known"])) for r in rows]
    unknown = [abs(int(r["actual_unknown"]) - int(r["shown_unknown"])) for r in rows]
    return {"samples": len(rows),
            "known_mae": round(statistics.mean(known), 2), "known_exact_pct": round(known.count(0) / len(rows) * 100, 1),
            "unknown_mae": round(statistics.mean(unknown), 2),
            "unknown_exact_pct": round(unknown.count(0) / len(rows) * 100, 1)}


def write_templates(folder: str):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "ground_truth.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("person,event_type,timestamp,notes\n")
    with open(os.path.join(folder, "occupancy_log.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("timestamp,actual_known,actual_unknown,shown_known,shown_unknown\n")
    print(f"Templates written to {folder}")


def to_markdown(report: Dict) -> str:
    lines = ["# AttendAI accuracy evaluation", "",
             f"Tolerance: ±{report['tolerance_seconds']} s. One labelled door crossing = one case.", "",
             "| Metric | Value |", "|---|---|"]
    for key in ("labelled_crossings", "enrolled_crossings", "visitor_crossings", "system_events", "matched",
                "missed", "wrong_identity", "spurious", "false_negatives", "false_positives",
                "visitors_recorded_as_students", "visitor_crossings_correctly_not_recorded", "denominator",
                "identification_accuracy", "recall", "precision"):
        value = report[key]
        lines.append(f"| {key.replace('_', ' ')} | {'n/a' if value is None else value}"
                     f"{'%' if key in ('identification_accuracy', 'recall', 'precision') and value is not None else ''} |")
    if report.get("timing_seconds"):
        t = report["timing_seconds"]
        lines.append(f"| timestamp error (s) | mean {t['mean']}, median {t['median']}, p95 {t['p95']}, max {t['max']} |")
    if report.get("occupancy"):
        o = report["occupancy"]
        lines.append(f"| occupancy samples | {o['samples']} |")
        lines.append(f"| known occupancy MAE | {o['known_mae']} (exact {o['known_exact_pct']}%) |")
        lines.append(f"| unregistered occupancy MAE | {o['unknown_mae']} (exact {o['unknown_exact_pct']}%) |")
    lines += ["", f"Identification accuracy = {report['accuracy_formula']}. False positives = wrong identity + "
              "spurious (duplicates, events with no crossing, visitors recorded as students). False negatives = "
              "missed enrolled crossings. Nothing is excluded from the denominator."]
    if report.get("manual_events_excluded") is not None:
        lines.append(f"Manual events in the window (operator corrections, not camera recognitions) excluded: "
                     f"{report['manual_events_excluded']}.")
    lines += ["", "Record the test conditions (lighting, camera angle, group sizes, occlusion, caps/masks) with "
              "these numbers. Results apply only to those conditions."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write-templates", metavar="FOLDER")
    parser.add_argument("--ground-truth")
    parser.add_argument("--db", help="SQLite database (opened read-only)")
    parser.add_argument("--events-csv", help="system events CSV instead of --db (person,event_type,timestamp)")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--tolerance", type=float, default=10.0)
    parser.add_argument("--occupancy-log")
    parser.add_argument("--out", help="write <out>.json and <out>.md")
    args = parser.parse_args(argv)

    if args.write_templates:
        write_templates(args.write_templates)
        return
    if not args.ground_truth or not (args.db or args.events_csv):
        parser.error("--ground-truth and one of --db / --events-csv are required")
    truth = read_crossings(args.ground_truth)
    if not truth:
        raise SystemExit("The ground truth file has no rows: label a real session first")
    start = parse_time(args.start) if args.start else min(c.at for c in truth)
    end = parse_time(args.end) if args.end else max(c.at for c in truth)
    manual_excluded = None
    if args.db:
        system, manual_excluded = _db_events(args.db, start, end)
    else:
        system = [c for c in read_crossings(args.events_csv) if start <= c.at <= end]
    report = evaluate(truth, system, args.tolerance)
    report["manual_events_excluded"] = manual_excluded
    report["window"] = {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds")}
    if args.occupancy_log:
        report["occupancy"] = evaluate_occupancy(args.occupancy_log)
    text = to_markdown(report)
    print(text)
    if args.out:
        with open(args.out + ".json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        with open(args.out + ".md", "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
