"""
Roster CSV import: template, dry-run validation report, all-or-nothing import,
duplicate/invalid row detection, and student list filters.
"""
from app import models
from helpers import add_student

HEADER = "student_id,name,class_name,department,person_type,is_active\n"


def upload(client, text, dry_run=True, filename="roster.csv"):
    return client.post(f"/students/import?dry_run={'true' if dry_run else 'false'}",
                       files={"file": (filename, text.encode("utf-8"), "text/csv")})


def test_template_is_valid_csv_that_imports(api):
    client, factory = api
    template = client.get("/students/import/template")
    assert template.status_code == 200 and template.headers["content-type"].startswith("text/csv")
    report = upload(client, template.text).json()
    assert report["errors"] == [] and report["valid_rows"] == 2


def test_dry_run_reports_without_writing_then_import_creates(api):
    client, factory = api
    text = HEADER + "21CSE001,Asha Raman,CSE-A,CSE,student,true\n21CSE002,Ravi K,CSE-A,CSE,,\nSTF1,Dr Rao,,CSE,staff,yes\n"
    report = upload(client, text).json()
    assert report == {"dry_run": True, "total_rows": 3, "valid_rows": 3, "errors": [], "created": 0}
    db = factory()
    assert db.query(models.Student).count() == 0
    db.close()

    result = upload(client, text, dry_run=False).json()
    assert result["created"] == 3
    db = factory()
    rows = {s.student_id: s for s in db.query(models.Student).all()}
    assert rows["STF1"].person_type == "staff" and rows["21CSE002"].person_type == "student"
    assert rows["21CSE002"].is_active is True and rows["21CSE001"].class_name == "CSE-A"
    assert db.query(models.AuditLog).filter_by(action="students.import").count() == 1
    db.close()


def test_invalid_rows_are_reported_and_nothing_is_imported(api):
    client, factory = api
    db = factory()
    add_student(db, "EXISTS")
    db.close()
    text = (HEADER
            + "A1,Alpha,,,,\n"
            + "A1,Alpha again,,,,\n"           # duplicate in file
            + "EXISTS,Clash,,,,\n"             # already in database
            + ",No ID,,,,\n"                   # missing id
            + "B2,,,,,\n"                      # missing name
            + "C3,Gamma,,,teacher,\n"          # bad person_type
            + "D4,Delta,,,,maybe\n"            # bad boolean
            + "E5," + "x" * 101 + ",,,,\n")   # too long
    result = upload(client, text, dry_run=False).json()
    assert result["created"] == 0 and result["valid_rows"] == 1 and result["total_rows"] == 8
    problems = {(e["row"], e["field"]) for e in result["errors"]}
    assert problems == {(3, "student_id"), (4, "student_id"), (5, "student_id"), (6, "name"),
                        (7, "person_type"), (8, "is_active"), (9, "name")}
    db = factory()
    assert db.query(models.Student).count() == 1  # only the pre-existing one
    db.close()


def test_bad_header_and_bad_files_are_rejected(api):
    client, _ = api
    report = upload(client, "id,fullname\n1,A\n").json()
    assert {e["field"] for e in report["errors"]} == {"header"}
    assert upload(client, "").json()["errors"][0]["message"] == "The file is empty"
    assert upload(client, HEADER).json()["errors"][0]["message"] == "No data rows found"
    assert upload(client, HEADER + "A,B\n", filename="roster.xlsx").status_code == 400
    too_big = HEADER + ("X,Y,,,,\n" * 200_000)
    assert upload(client, too_big).status_code == 413


def test_student_filters_and_enrollment_status(api):
    client, factory = api
    db = factory()
    enrolled = add_student(db, "E1", name="Enrolled Person")
    enrolled.class_name = "CSE-A"
    db.add(models.FaceEmbedding(student_id=enrolled.id, embedding_data=b"\x00" * 1024))
    staff = add_student(db, "S1", name="Staff Person")
    staff.person_type = "staff"
    db.commit()
    db.close()

    everyone = {s["student_id"]: s for s in client.get("/students/").json()}
    assert everyone["E1"]["enrolled_faces"] == 1 and everyone["S1"]["enrolled_faces"] == 0
    assert "embedding_data" not in str(everyone)
    assert [s["student_id"] for s in client.get("/students/?enrolled=false").json()] == ["S1"]
    assert [s["student_id"] for s in client.get("/students/?person_type=staff").json()] == ["S1"]
    assert [s["student_id"] for s in client.get("/students/?q=enrolled").json()] == ["E1"]
    assert client.get("/students/classes").json() == ["CSE-A"]


def test_student_ids_and_names_are_trimmed_and_blank_rejected(api):
    client, _ = api
    r = client.post("/students/", json={"student_id": "  T7 ", "name": " Tara "})
    assert r.status_code == 200 and r.json()["student_id"] == "T7" and r.json()["name"] == "Tara"
    assert client.post("/students/", json={"student_id": "   ", "name": "Blank"}).status_code == 422
    assert client.post("/students/", json={"student_id": "T8", "name": "X", "person_type": "alien"}).status_code == 422
