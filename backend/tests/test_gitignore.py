"""
attendai/.gitignore keeps secrets and personal data out of Git, and keeps the
secret-free .env.example template tracked.

Uses `git check-ignore` in a throwaway repository containing a copy of the
.gitignore and EMPTY placeholder files - no real file contents are copied.
"""
import glob
import os
import shutil
import subprocess

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(BACKEND_DIR)  # attendai/
GITIGNORE = os.path.join(PROJECT_DIR, ".gitignore")

git = shutil.which("git")
pytestmark = pytest.mark.skipif(git is None, reason="git not installed")

MUST_IGNORE = [
    ".env",
    "backend/.env",
    "backend/.env.test",
    "frontend/.env",
    "backend/data/attendai.db",
    "backend/data/attendai.db.bak-20261019-201115-pre-d5a1e7c2b9f4",
    "backend/data/attendai.db-wal",
    "backend/data/attendai.db-shm",
    "backend/data/attendai.sqlite.backup",
    "backend/data/faces/student1.jpg",
    "backend/data/embeddings/student1.npy",
    "backend/data/videos/class.mp4",
    "data/faces/student1.jpg",
    "private/account_audit_2026-10-20.md",
]
MUST_TRACK = [
    ".env.example",
    "backend/data/faces/.gitkeep",
    "backend/data/embeddings/.gitkeep",
    "backend/app/main.py",
    "backend/requirements.txt",
    "backend/alembic/versions/d5a1e7c2b9f4_soft_delete_enrollments_timestamp_index.py",
]


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("gitcheck")
    subprocess.run([git, "init", "-q", str(root)], check=True)
    project = root / "attendai"
    project.mkdir()
    shutil.copyfile(GITIGNORE, project / ".gitignore")
    # Also check the names of any real backups that exist next to the database
    real_backups = [os.path.relpath(p, PROJECT_DIR).replace(os.sep, "/")
                    for p in glob.glob(os.path.join(BACKEND_DIR, "data", "*.db.*"))]
    for rel in MUST_IGNORE + MUST_TRACK + real_backups:
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()  # empty placeholder
    return root, real_backups


def is_ignored(root, rel):
    # --no-index: judge by patterns only; core.excludesFile disabled so the
    # developer's global ignore file does not influence the result
    result = subprocess.run([git, "-c", "core.excludesFile=", "check-ignore", "--no-index", "-q", f"attendai/{rel}"],
                            cwd=root, capture_output=True)
    return result.returncode == 0


@pytest.mark.parametrize("rel", MUST_IGNORE)
def test_secrets_and_personal_data_are_ignored(repo, rel):
    root, _ = repo
    assert is_ignored(root, rel), f"{rel} would be committed"


@pytest.mark.parametrize("rel", MUST_TRACK)
def test_source_and_template_are_tracked(repo, rel):
    root, _ = repo
    assert not is_ignored(root, rel), f"{rel} is ignored but should be tracked"


def test_existing_database_backups_are_ignored(repo):
    root, real_backups = repo
    for rel in real_backups:
        assert is_ignored(root, rel), f"{rel} would be committed"


def test_env_example_contains_no_secret():
    with open(os.path.join(PROJECT_DIR, ".env.example"), encoding="utf-8") as f:
        values = dict(line.strip().split("=", 1) for line in f
                      if "=" in line and not line.lstrip().startswith("#"))
    assert values.get("SECRET_KEY", "") == ""
