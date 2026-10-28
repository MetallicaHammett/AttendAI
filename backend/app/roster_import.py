"""
Class roster CSV import: parsing and row-level validation (no database writes here).

Template (header row required, column order free, extra columns rejected):
    student_id,name,class_name,department,person_type,is_active
    21CSE001,Asha Raman,CSE-A,Computer Science,student,true

- student_id, name: required; student_id must be unique in the file and in the database
  (including deleted records, whose IDs stay reserved for their history).
- person_type: student (default) or staff.
- is_active: true/false/yes/no/1/0 (default true).
"""
import csv
import io
from dataclasses import dataclass, field
from typing import Dict, List, Set

COLUMNS = ("student_id", "name", "class_name", "department", "person_type", "is_active")
REQUIRED = ("student_id", "name")
MAX_LENGTHS = {"student_id": 50, "name": 100, "class_name": 100, "department": 100}
MAX_IMPORT_BYTES = 1024 * 1024  # 1 MB
MAX_ROWS = 5000
TRUE_VALUES = {"true", "yes", "1", "y"}
FALSE_VALUES = {"false", "no", "0", "n"}

TEMPLATE_CSV = (
    "student_id,name,class_name,department,person_type,is_active\r\n"
    "21CSE001,Example Student,CSE-A,Computer Science,student,true\r\n"
    "STAFF-001,Example Staff Member,,Computer Science,staff,true\r\n"
)


@dataclass
class RowError:
    row: int  # 1-based line number in the file (header = line 1)
    field: str
    message: str


@dataclass
class ImportReport:
    total_rows: int = 0
    rows: List[Dict] = field(default_factory=list)  # validated rows ready for models.Student(**row)
    errors: List[RowError] = field(default_factory=list)


def _decode(contents: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return contents.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("File is not valid UTF-8 text")


def parse_roster_csv(contents: bytes, existing_ids: Set[str]) -> ImportReport:
    report = ImportReport()
    try:
        text = _decode(contents)
    except ValueError as e:
        report.errors.append(RowError(0, "file", str(e)))
        return report
    if not text.strip():
        report.errors.append(RowError(0, "file", "The file is empty"))
        return report

    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in header]
    unknown = [h for h in header if h not in COLUMNS]
    if missing or unknown:
        if missing:
            report.errors.append(RowError(1, "header", f"Missing required column(s): {', '.join(missing)}"))
        if unknown:
            report.errors.append(RowError(1, "header", f"Unknown column(s): {', '.join(unknown)}. "
                                                       f"Allowed: {', '.join(COLUMNS)}"))
        return report
    reader.fieldnames = header

    seen: Dict[str, int] = {}
    for line_no, raw in enumerate(reader, start=2):
        if report.total_rows >= MAX_ROWS:
            report.errors.append(RowError(line_no, "file", f"Too many rows (maximum {MAX_ROWS})"))
            break
        if raw.get(None):  # more values than header columns
            report.errors.append(RowError(line_no, "row", "Row has more values than the header"))
            report.total_rows += 1
            continue
        values = {k: (v or "").strip() for k, v in raw.items() if k is not None}
        if not any(values.values()):
            continue  # blank line
        report.total_rows += 1
        row_errors = []

        for column in REQUIRED:
            if not values.get(column):
                row_errors.append(RowError(line_no, column, "Required"))
        for column, limit in MAX_LENGTHS.items():
            if len(values.get(column, "")) > limit:
                row_errors.append(RowError(line_no, column, f"Longer than {limit} characters"))

        student_id = values.get("student_id", "")
        if student_id:
            if student_id in existing_ids:
                row_errors.append(RowError(line_no, "student_id", f"'{student_id}' already exists"))
            if student_id in seen:
                row_errors.append(RowError(line_no, "student_id",
                                           f"Duplicate of line {seen[student_id]} in this file"))
            else:
                seen[student_id] = line_no

        person_type = (values.get("person_type") or "student").lower()
        if person_type not in ("student", "staff"):
            row_errors.append(RowError(line_no, "person_type", "Must be 'student' or 'staff'"))

        active_text = (values.get("is_active") or "true").lower()
        if active_text not in TRUE_VALUES | FALSE_VALUES:
            row_errors.append(RowError(line_no, "is_active", "Must be true or false"))

        if row_errors:
            report.errors.extend(row_errors)
            continue
        report.rows.append({
            "student_id": student_id,
            "name": values["name"],
            "class_name": values.get("class_name") or None,
            "department": values.get("department") or None,
            "person_type": person_type,
            "is_active": active_text in TRUE_VALUES,
        })

    if report.total_rows == 0 and not report.errors:
        report.errors.append(RowError(0, "file", "No data rows found"))
    return report
