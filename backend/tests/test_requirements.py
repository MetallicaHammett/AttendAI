"""
The interpreter running the tests satisfies requirements.txt.

Expected to pass in the reference environment (.venv, Python 3.12). It is
expected to FAIL in an environment with known-bad versions (e.g. the `venv`
folder: bcrypt 5.0 breaks passlib, setuptools 84 lacks pkg_resources).
"""
import os
from importlib.metadata import PackageNotFoundError, version

import pytest

packaging_requirements = pytest.importorskip("packaging.requirements")

REQUIREMENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "requirements.txt")


def parse_requirements():
    reqs = []
    with open(REQUIREMENTS, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                reqs.append(packaging_requirements.Requirement(line))
    return reqs


def test_requirements_file_parses_and_covers_runtime_imports():
    names = {r.name.lower().replace("_", "-") for r in parse_requirements()}
    for needed in ("fastapi", "sqlalchemy", "alembic", "pydantic-settings", "python-jose", "passlib", "bcrypt",
                   "numpy", "opencv-python", "ultralytics", "deep-sort-realtime", "dlib", "face-recognition",
                   "face-recognition-models", "setuptools", "python-multipart", "uvicorn"):
        assert needed in names, f"{needed} missing from requirements.txt"


def test_installed_versions_satisfy_requirements():
    problems = []
    for req in parse_requirements():
        try:
            installed = version(req.name)
        except PackageNotFoundError:
            problems.append(f"{req.name}: not installed")
            continue
        if not req.specifier.contains(installed, prereleases=True):
            problems.append(f"{req.name}=={installed} does not satisfy '{req.specifier}'")
    assert problems == []
