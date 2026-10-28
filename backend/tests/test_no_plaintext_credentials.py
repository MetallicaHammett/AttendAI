"""
No plaintext passwords or bearer tokens in hand-written project files outside the automated
test suites (which only use disposable values against throwaway databases). Guards the
root-level scripts, data files, docs and local tool settings folders, where real test account
passwords were once recorded. Failure messages name the file and line, never the value.
"""
import glob
import os
import re

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(BACKEND_DIR)

CHECKED = (
    glob.glob(os.path.join(BACKEND_DIR, "*.py")) + glob.glob(os.path.join(BACKEND_DIR, "*.json"))
    + glob.glob(os.path.join(BACKEND_DIR, "scripts", "*.py")) + glob.glob(os.path.join(PROJECT_DIR, "docs", "**", "*.md"), recursive=True)
    + glob.glob(os.path.join(PROJECT_DIR, "*.md")) + glob.glob(os.path.join(PROJECT_DIR, "**", ".*", "settings*.json"), recursive=True)
)

# A quoted literal after password: / password= (JSON, dicts, kwargs), or a form/query body
# such as "username=x&password=y" (code such as hashed_password=get_password_hash(...) is fine)
PASSWORD = re.compile(r"""(?i)["']?\bpassword["']?\s*[:=]\s*["']([^"'\s]{4,})["']|[?&"']password=([^&"'\s\\)]{4,})""")
JWT = re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")
# Placeholders and code are fine; a literal password is not
ALLOWED = re.compile(r"(?i)^(<[^>]*>|\$\{?\w+\}?|\*+|x+|\.\.\.|your[-_].*|change[-_]?me.*|form\.\w+.*|args\.\w+.*)$")


def findings():
    for path in CHECKED:
        if os.sep + "node_modules" + os.sep in path or os.sep + ".venv" + os.sep in path:
            continue
        with open(path, encoding="utf-8", errors="ignore") as f:
            for n, line in enumerate(f, 1):
                for m in PASSWORD.finditer(line):
                    value = m.group(1) or m.group(2)
                    if not ALLOWED.match(value):
                        yield f"{os.path.relpath(path, PROJECT_DIR)}:{n} (password literal)"
                if JWT.search(line):
                    yield f"{os.path.relpath(path, PROJECT_DIR)}:{n} (bearer token)"


def test_checked_files_exist():
    names = {os.path.basename(p) for p in CHECKED}
    assert "manage_users.py" in names


def test_no_plaintext_passwords_or_tokens():
    found = list(findings())
    assert found == [], "Plaintext credentials (values not shown): " + ", ".join(found)
