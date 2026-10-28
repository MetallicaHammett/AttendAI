"""
scripts/batch_enroll.py: folder-name parsing (register numbers, year, nothing guessed), the
dry run writes nothing, and apply registers/enrolls only confirmed entries through the app's
own services. Real-model parts use the SFace files and matplotlib's public-domain sample
portrait; temporary database and folders only.
"""
import json
import os
import sys

import cv2
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import batch_enroll  # noqa: E402

from app import models  # noqa: E402
from test_sface import HOPPER, real_models  # noqa: E402


@pytest.mark.parametrize("folder,number,year,flagged", [
    ("Anirudh Kumar 14", "RA2611030040014", 1, False),
    ("Harshvardhan Singh Sisodia 50", "RA2611030040050", 1, False),
    ("Kishore 7", "RA2611030040007", 1, True),                         # one digit: proposed 07, needs confirmation
    ("SURYA SWAMINATHAN 2ND YEAR CYBER SECURITY 08", "RA2511030040008", 2, False),
    ("surya swminathn 08", "RA2511030040008", 2, False),               # misspelt, still the named 2nd-year student
    ("Jeyasurya 57", "RA2611030040057", 1, False),                     # contains "surya" but is a first-year student
])
def test_folder_names_give_exact_register_numbers(folder, number, year, flagged):
    entry = batch_enroll.parse_folder(folder)
    assert entry["register_number"] == number and entry["year"] == year
    assert bool(entry["flags"]) == flagged


@pytest.mark.parametrize("folder,reason", [("No Digits Here", "no register-number suffix"), ("Name 123", "not two digits")])
def test_unusable_folder_names_are_flagged_and_get_no_number(folder, reason):
    entry = batch_enroll.parse_folder(folder)
    assert "register_number" not in entry and any(reason in f for f in entry["flags"])


def test_second_year_name_is_the_official_spelling():
    entry = batch_enroll.parse_folder("surya swminathn 08")
    assert entry["name"] == "Surya Swaminathan" and "read as Surya Swaminathan" in entry["name_note"]


@pytest.fixture
def dataset(tmp_path):
    """Two people: 'Grace Hopper 05' with two (different-bytes) photos, 'Empty Person 06' with none,
    and 'Kishore 7' with a one-digit suffix"""
    src = tmp_path / "photos"
    for folder in ("Grace Hopper 05", "Empty Person 06", "Kishore 7"):
        (src / folder).mkdir(parents=True)
    img = cv2.imread(HOPPER) if HOPPER else None
    if img is not None:
        cv2.imwrite(str(src / "Grace Hopper 05" / "a.jpg"), img)
        cv2.imwrite(str(src / "Grace Hopper 05" / "b.jpg"), cv2.convertScaleAbs(img, alpha=0.9, beta=8))
        cv2.imwrite(str(src / "Kishore 7" / "a.jpg"), cv2.flip(img, 1))
    (src / "Grace Hopper 05" / "notes.txt").write_text("not a photo")
    return src


@pytest.fixture
def wired(api, sface_active, monkeypatch):
    """batch_enroll talking to the isolated test database"""
    _, factory = api
    import app.database
    monkeypatch.setattr(app.database, "SessionLocal", factory)
    monkeypatch.setattr(app.database, "check_schema_version", lambda: {"state": "up_to_date"})
    return factory


@real_models
def test_dry_run_inventory_writes_nothing_and_flags_problems(wired, dataset, tmp_path):
    factory = wired
    batch_enroll.inventory(str(dataset), str(tmp_path / "out"))
    plan = json.load(open(tmp_path / "out" / "plan.json", encoding="utf-8"))
    by_folder = {e["folder"]: e for e in plan["entries"]}
    hopper = by_folder["Grace Hopper 05"]
    assert hopper["register_number"] == "RA2611030040005" and hopper["usable_photos"] == 2 and hopper["status"] == "ready"
    assert any(p["file"] == "notes.txt" and p["reason"] == "not a JPEG/PNG image" for p in hopper["photos"])
    assert by_folder["Empty Person 06"]["status"] == "review"
    assert by_folder["Kishore 7"]["status"] == "review"
    assert (tmp_path / "out" / "inventory.md").read_text(encoding="utf-8").startswith("# Enrollment inventory (DRY RUN")
    db = factory()
    assert db.query(models.Student).count() == 0 and db.query(models.FaceEmbedding).count() == 0
    db.close()


@real_models
def test_apply_registers_and_enrolls_only_confirmed_entries(wired, dataset, tmp_path):
    factory = wired
    batch_enroll.inventory(str(dataset), str(tmp_path / "out"))
    plan = str(tmp_path / "out" / "plan.json")
    # An entry still under review is refused without changing anything
    with pytest.raises(SystemExit, match="needs review"):
        batch_enroll.apply(plan, ["RA2611030040006"], accept_padded=False, out_dir=str(tmp_path / "out"))
    with pytest.raises(SystemExit, match="needs review"):
        batch_enroll.apply(plan, ["RA2611030040007"], accept_padded=False, out_dir=str(tmp_path / "out"))
    db = factory()
    assert db.query(models.Student).count() == 0
    db.close()

    batch_enroll.apply(plan, ["RA2611030040005"], accept_padded=False, out_dir=str(tmp_path / "out"))
    db = factory()
    student = db.query(models.Student).filter_by(student_id="RA2611030040005").one()
    assert (student.name, student.class_name, student.department) == \
        ("Grace Hopper", "CSE Cybersecurity", "CSE - Emerging Technologies")
    assert student.enrolled_faces == 2
    assert all(len(e.embedding_data) == 512 for e in student.face_embeddings)  # SFace only
    assert db.query(models.AttendanceEvent).count() == 0  # enrollment never records attendance
    assert db.query(models.AuditLog).filter_by(action="student.create", username="console:batch_enroll").count() == 1
    db.close()

    # Re-running never creates a second record for the same register number
    batch_enroll.apply(plan, ["RA2611030040005"], accept_padded=False, out_dir=str(tmp_path / "out"))
    db = factory()
    assert db.query(models.Student).filter_by(student_id="RA2611030040005").count() == 1
    db.close()

    # Record-only for an empty folder, only when explicitly allowed
    batch_enroll.apply(plan, ["RA2611030040006"], accept_padded=False, out_dir=str(tmp_path / "out"), allow_no_photos=True)
    db = factory()
    empty = db.query(models.Student).filter_by(student_id="RA2611030040006").one()
    assert empty.enrolled_faces == 0
    db.close()


@real_models
def test_retrying_refused_photos_never_enrolls_a_photo_twice(wired, dataset, tmp_path):
    factory = wired
    out = tmp_path / "out"
    batch_enroll.inventory(str(dataset), str(out))
    plan = str(out / "plan.json")
    batch_enroll.apply(plan, ["RA2611030040005"], accept_padded=False, out_dir=str(out))
    db = factory()
    before = db.query(models.FaceEmbedding).count()
    db.close()
    assert before == 2
    batch_enroll.apply(plan, ["RA2611030040005"], accept_padded=False, out_dir=str(out), only_refused=True)
    db = factory()
    assert db.query(models.FaceEmbedding).count() == before  # both photos were already enrolled
    db.close()
    assert batch_enroll.already_enrolled(str(out)) == {("RA2611030040005", "a.jpg"), ("RA2611030040005", "b.jpg")}


@real_models
def test_apply_refuses_to_create_a_second_year_record(wired, tmp_path):
    src = tmp_path / "photos" / "surya swminathn 08"
    src.mkdir(parents=True)
    cv2.imwrite(str(src / "a.jpg"), cv2.imread(HOPPER))
    batch_enroll.inventory(str(tmp_path / "photos"), str(tmp_path / "out"))
    with pytest.raises(SystemExit, match="second-year record must already exist"):
        batch_enroll.apply(str(tmp_path / "out" / "plan.json"), ["RA2511030040008"], accept_padded=False,
                           out_dir=str(tmp_path / "out"))
