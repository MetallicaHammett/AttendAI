"""
Batch student registration and face enrollment from a folder of per-student photo folders.

    # 1. Dry run (default, writes NOTHING to the database): inventory + plan
    .\\.venv\\Scripts\\python.exe scripts\\batch_enroll.py inventory --source "C:\\path\\to\\photos" --out ..\\private\\enroll
    # 2. Review ..\\private\\enroll\\inventory.md, then apply only confirmed entries
    .\\.venv\\Scripts\\python.exe scripts\\batch_enroll.py apply --plan ..\\private\\enroll\\plan.json --only RA2611030040014,...

Folder naming convention: "<student name> <register-number suffix>", e.g. "Arun Kumar 42".
The suffix is the last two digits of the register number:
    first year   RA26110300400NN  (class CSE Cybersecurity)
    second year  RA25110300400NN  (only the named second-year student, see SECOND_YEAR)
Nothing is guessed: a missing, one-digit or longer suffix, an unexpected name, a suffix used
twice in the same year, or a folder with no usable photo is flagged for review and is never
applied unless explicitly confirmed with --only (and --accept-padded for one-digit suffixes).

Uses the application's own services: students.create_student (duplicate-ID check, audit) and
face_enrollment.enroll_image / analyse_photo (the active recognition model, the same
detection, alignment, quality checks and look-alike checks as uploads). Source photos are
only read. No attendance event is ever created.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)  # .env and relative paths resolve like the server

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from fastapi import HTTPException  # noqa: E402

FIRST_YEAR = {"year": 1, "prefix": "RA26110300400", "class_name": "CSE Cybersecurity",
              "department": "CSE - Emerging Technologies"}
SECOND_YEAR = {"year": 2, "prefix": "RA25110300400", "official_name": "Surya Swaminathan"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
FOLDER_RE = re.compile(r"^(?P<name>.*?\D)\s*(?P<digits>\d+)\s*$")
CONSOLE = {"id": None, "username": "console:batch_enroll"}


def is_second_year_folder(folder: str) -> bool:
    """The one named second-year student (spelling may vary: 'surya swminathn' ...)"""
    text = folder.lower()
    return "surya" in text and re.search(r"sw[a-z]*m[a-z]*n", text) is not None


def parse_folder(folder: str) -> dict:
    entry = {"folder": folder, "flags": []}
    match = FOLDER_RE.match(folder.strip())
    second = is_second_year_folder(folder)
    if match is None:
        entry["flags"].append("no register-number suffix in the folder name")
        entry["name"] = folder.strip()
        return entry
    raw_name, digits = match.group("name").strip(), match.group("digits")
    if second:
        entry.update(name=SECOND_YEAR["official_name"], year=2)
        if raw_name.lower() != SECOND_YEAR["official_name"].lower():
            entry["name_note"] = f"folder name '{raw_name}' read as {SECOND_YEAR['official_name']}"
    else:
        entry.update(name=raw_name, year=1)
        if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", raw_name):
            entry["flags"].append("name contains unexpected characters")
    entry["suffix_raw"] = digits
    if len(digits) == 2:
        entry["suffix"] = digits
    elif len(digits) == 1:
        entry["suffix"] = "0" + digits
        entry["padded"] = True
        entry["flags"].append(f"one-digit suffix '{digits}': proposed '0{digits}' needs confirmation")
    else:
        entry["flags"].append(f"suffix '{digits}' is not two digits")
    if "suffix" in entry:
        prefix = SECOND_YEAR["prefix"] if second else FIRST_YEAR["prefix"]
        entry["register_number"] = prefix + entry["suffix"]
    return entry


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_image(path: str):
    """Decode a photo the way an upload is decoded (source file is only read)"""
    from app.routers.face_enrollment import decode_image
    with open(path, "rb") as f:
        return decode_image(f.read())


def inventory(source: str, out_dir: str):
    from app.database import SessionLocal
    from app import models
    from app.routers.face_enrollment import analyse_photo
    from app import face_models

    folders = sorted(d for d in os.listdir(source) if os.path.isdir(os.path.join(source, d)))
    stray = sorted(f for f in os.listdir(source) if not os.path.isdir(os.path.join(source, f)))
    entries, hashes = [], {}
    for folder in folders:
        entry = parse_folder(folder)
        photos = []
        for name in sorted(os.listdir(os.path.join(source, folder))):
            path = os.path.join(source, folder, name)
            if os.path.isdir(path):
                entry["flags"].append(f"unexpected subfolder '{name}'")
                continue
            photo = {"file": name, "bytes": os.path.getsize(path)}
            if os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
                photo.update(usable=False, reason="not a JPEG/PNG image")
                photos.append(photo)
                continue
            photo["sha256"] = sha256(path)
            hashes.setdefault(photo["sha256"], []).append(f"{folder}/{name}")
            try:
                image = read_image(path)
                photo["size"] = f"{image.shape[1]}x{image.shape[0]}"
                _, _, quality = analyse_photo(image)
                photo.update(usable=True, quality=quality)
            except HTTPException as e:
                photo.update(usable=False, reason=str(e.detail))
            photos.append(photo)
        entry["photos"] = photos
        entry["usable_photos"] = sum(1 for p in photos if p.get("usable"))
        if not photos:
            entry["flags"].append("folder has no photos")
        elif entry["usable_photos"] == 0:
            entry["flags"].append("no photo passed the enrollment checks")
        entries.append(entry)

    # Exact duplicate files (same bytes) anywhere in the dataset
    for digest, places in hashes.items():
        if len(places) > 1:
            for entry in entries:
                for photo in entry["photos"]:
                    if photo.get("sha256") == digest:
                        photo["duplicate_of"] = [p for p in places if p != f"{entry['folder']}/{photo['file']}"]
                        if any(not p.startswith(entry["folder"] + "/") for p in photo["duplicate_of"]):
                            entry["flags"].append(f"photo {photo['file']} is identical to a photo in another folder")
    # Same register number twice (same suffix within one year)
    by_number = {}
    for entry in entries:
        if entry.get("register_number"):
            by_number.setdefault(entry["register_number"], []).append(entry["folder"])
    for entry in entries:
        others = [f for f in by_number.get(entry.get("register_number"), []) if f != entry["folder"]]
        if others:
            entry["flags"].append(f"register number also derived from folder(s): {', '.join(others)}")

    # Existing database records (read only): match by the complete register number
    db = SessionLocal()
    try:
        model = face_models.active_model()
        for entry in entries:
            number = entry.get("register_number")
            if not number:
                continue
            rows = db.query(models.Student).filter(models.Student.student_id == number).all()
            if len(rows) > 1:
                entry["flags"].append("several database records share this register number")
            elif rows:
                s = rows[0]
                faces = {m: sum(1 for e in s.face_embeddings if e.is_active and face_models.model_of(e.embedding_data) == m)
                         for m in face_models.MODELS}
                entry["existing"] = {"id": s.id, "name": s.name, "class_name": s.class_name, "department": s.department,
                                     "is_active": s.is_active, "deleted": s.deleted_at is not None,
                                     "faces": faces}
                if s.name.strip().lower() != entry["name"].strip().lower():
                    entry["flags"].append(f"database record #{s.id} has the name '{s.name}'")
                if s.deleted_at is not None or not s.is_active:
                    entry["flags"].append(f"database record #{s.id} is inactive or deleted")
                if faces[model]:
                    entry["flags"].append(f"already has {faces[model]} {model} face(s); new photos would be added")
            # Same name under a different number (possible duplicate person)
            same_name = db.query(models.Student).filter(models.Student.name.ilike(entry["name"]),
                                                        models.Student.student_id != number).all()
            if same_name:
                entry["flags"].append("a student with the same name exists under another number: "
                                      + ", ".join(f"#{s.id} {s.student_id}" for s in same_name))
    finally:
        db.close()

    for entry in entries:
        entry["status"] = "ready" if not entry["flags"] else "review"
        if entry.get("year") == 1:
            entry["class_name"], entry["department"] = FIRST_YEAR["class_name"], FIRST_YEAR["department"]
        elif entry.get("year") == 2 and entry.get("existing"):
            entry["class_name"], entry["department"] = entry["existing"]["class_name"], entry["existing"]["department"]
        entry["action"] = ("reuse #%d" % entry["existing"]["id"]) if entry.get("existing") else "create"

    os.makedirs(out_dir, exist_ok=True)
    plan = {"source": os.path.abspath(source), "created": datetime.now().isoformat(timespec="seconds"),
            "recognition_model": face_models.active_model(), "stray_files": stray, "entries": entries}
    with open(os.path.join(out_dir, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1)
    with open(os.path.join(out_dir, "inventory.md"), "w", encoding="utf-8") as f:
        f.write(render_inventory(plan))
    total = sum(len(e["photos"]) for e in entries)
    usable = sum(e["usable_photos"] for e in entries)
    print(f"{len(entries)} folders | {total} photos, {usable} pass the enrollment checks | "
          f"{sum(e['status'] == 'ready' for e in entries)} ready, {sum(e['status'] == 'review' for e in entries)} "
          f"need review | written to {os.path.abspath(out_dir)} (nothing written to the database)")


def render_inventory(plan: dict) -> str:
    lines = [f"# Enrollment inventory (DRY RUN - nothing written)", "",
             f"Source: `{plan['source']}`  ", f"Created: {plan['created']}  ",
             f"Recognition model: {plan['recognition_model']}", "",
             "| Folder | Name | Suffix | Register number | Year / class | Photos usable | Database | Status | Notes |",
             "|---|---|---|---|---|---|---|---|---|"]
    for e in plan["entries"]:
        db = f"reuse #{e['existing']['id']}" if e.get("existing") else "new"
        notes = "; ".join(e["flags"] + ([e["name_note"]] if e.get("name_note") else []))
        lines.append(f"| {e['folder']} | {e.get('name', '')} | {e.get('suffix_raw', '')} | {e.get('register_number', '—')} | "
                     f"{e.get('year', '?')} / {e.get('class_name', '?')} | {e['usable_photos']}/{len(e['photos'])} | {db} | "
                     f"{e['status']} | {notes or '—'} |")
    lines += ["", "## Photos not usable", ""]
    for e in plan["entries"]:
        for p in e["photos"]:
            if not p.get("usable"):
                lines.append(f"- {e['folder']}/{p['file']}: {p.get('reason')}")
    if plan["stray_files"]:
        lines += ["", "Files directly in the source folder (ignored): " + ", ".join(plan["stray_files"])]
    return "\n".join(lines) + "\n"


def already_enrolled(out_dir: str) -> set:
    """(register number, file) pairs enrolled by earlier apply runs (their apply-*.json results)"""
    done = set()
    for name in sorted(os.listdir(out_dir)):
        if name.startswith("apply-") and name.endswith(".json"):
            for row in json.load(open(os.path.join(out_dir, name), encoding="utf-8")):
                done.update((row["register_number"], e["file"]) for e in row["enrolled"])
    return done


def apply(plan_path: str, only: list, accept_padded: bool, out_dir: str, allow_no_photos: bool = False,
          only_refused: bool = False):
    """Create missing students and enroll photos for the listed register numbers only.
    only_refused: skip photos an earlier run already enrolled (retry the refused ones only)."""
    from app.database import SessionLocal, check_schema_version
    from app import models, schemas
    from app.routers import students as students_router
    from app.routers.face_enrollment import enroll_image

    schema = check_schema_version()
    if schema["state"] == "outdated":
        sys.exit("The database schema is outdated; run the migration first. Nothing was changed.")
    plan = json.load(open(plan_path, encoding="utf-8"))
    wanted = set(only)
    chosen = [e for e in plan["entries"] if e.get("register_number") in wanted]
    missing = wanted - {e.get("register_number") for e in chosen}
    if missing:
        sys.exit(f"Not in the plan: {', '.join(sorted(missing))}. Nothing was changed.")
    for e in chosen:
        blocking = [f for f in e["flags"] if not (accept_padded and f.startswith("one-digit suffix"))
                    and not (allow_no_photos and f == "folder has no photos")
                    and not f.startswith("already has")]
        if blocking:
            sys.exit(f"{e['folder']} still needs review ({'; '.join(blocking)}). Nothing was changed.")

    done = already_enrolled(out_dir) if only_refused else set()
    results = []
    db = SessionLocal()
    try:
        for e in chosen:
            number = e["register_number"]
            student = db.query(models.Student).filter(models.Student.student_id == number).first()
            created = False
            if student is None:
                if e["year"] != 1:
                    sys.exit(f"{e['folder']}: a second-year record must already exist; not creating one.")
                payload = schemas.StudentCreate(student_id=number, name=e["name"], class_name=FIRST_YEAR["class_name"],
                                                department=FIRST_YEAR["department"], person_type="student")
                student = asyncio.run(students_router.create_student(student=payload, db=db, current_user=CONSOLE))
                student = db.query(models.Student).filter(models.Student.id == student.id).first()
                created = True
            elif student.deleted_at is not None or not student.is_active:
                sys.exit(f"{number} exists but is inactive/deleted; not enrolling. Earlier entries were applied.")
            row = {"folder": e["folder"], "register_number": number, "student_db_id": student.id,
                   "created": created, "enrolled": [], "rejected": []}
            for photo in e["photos"]:
                if (number, photo["file"]) in done:
                    continue  # enrolled by an earlier run: never enrolled twice
                if not photo.get("usable"):
                    row["rejected"].append({"file": photo["file"], "reason": photo.get("reason")})
                    continue
                if photo.get("duplicate_of") and any(d.startswith(e["folder"] + "/") and d < f"{e['folder']}/{photo['file']}"
                                                     for d in photo["duplicate_of"]):
                    row["rejected"].append({"file": photo["file"], "reason": "identical copy of another photo in the folder"})
                    continue
                try:
                    image = read_image(os.path.join(plan["source"], e["folder"], photo["file"]))
                    result = enroll_image(db, student, image, CONSOLE, confirm_warnings=False)
                    row["enrolled"].append({"file": photo["file"], "embedding_id": result.id})
                except HTTPException as err:
                    db.rollback()
                    row["rejected"].append({"file": photo["file"], "reason": str(err.detail)})
            results.append(row)
            print(f"{number} {'created' if created else 'reused'} #{student.id}: "
                  f"{len(row['enrolled'])} enrolled, {len(row['rejected'])} rejected")
    finally:
        db.close()
    os.makedirs(out_dir, exist_ok=True)
    # Never overwrite an earlier run's results: --only-refused relies on every one of them
    stamp, n = datetime.now().strftime("%Y%m%d-%H%M%S"), 0
    path = os.path.join(out_dir, f"apply-{stamp}.json")
    while os.path.exists(path):
        n += 1
        path = os.path.join(out_dir, f"apply-{stamp}-{n}.json")
    with open(path, "x", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    print(f"Results written to {os.path.abspath(path)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Batch student registration and face enrollment")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inventory", help="dry run: inventory and plan, writes nothing to the database")
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True, help="folder for plan.json and inventory.md (keep it private)")
    p = sub.add_parser("apply", help="create/enroll the listed register numbers from a reviewed plan")
    p.add_argument("--plan", required=True)
    p.add_argument("--only", required=True, help="comma-separated register numbers to apply")
    p.add_argument("--accept-padded", action="store_true", help="accept one-digit suffixes padded with a leading 0")
    p.add_argument("--allow-no-photos", action="store_true",
                   help="register listed students whose folder has no photos (record only, not enrolled)")
    p.add_argument("--only-refused", action="store_true",
                   help="retry: skip photos already enrolled by earlier apply runs (apply-*.json next to the plan)")
    args = parser.parse_args(argv)
    if args.command == "inventory":
        inventory(args.source, args.out)
    else:
        apply(args.plan, [x.strip() for x in args.only.split(",") if x.strip()], args.accept_padded,
              os.path.dirname(os.path.abspath(args.plan)), allow_no_photos=args.allow_no_photos,
              only_refused=args.only_refused)


if __name__ == "__main__":
    main()
