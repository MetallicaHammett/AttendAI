"""
Account administration from the server console (no API token needed).

Run from the backend folder with the project's environment, e.g.:

    .\\.venv\\Scripts\\python.exe scripts\\manage_users.py list
    .\\.venv\\Scripts\\python.exe scripts\\manage_users.py set-role alice admin
    .\\.venv\\Scripts\\python.exe scripts\\manage_users.py create bob bob@example.edu --role operator
    .\\.venv\\Scripts\\python.exe scripts\\manage_users.py create prof_rao rao@example.edu --role professor
    .\\.venv\\Scripts\\python.exe scripts\\manage_users.py create asha asha@example.edu --student RA2511030040008
    .\\.venv\\Scripts\\python.exe scripts\\manage_users.py deactivate bob
    .\\.venv\\Scripts\\python.exe scripts\\manage_users.py set-password alice   # hidden prompt, typed twice

It uses DATABASE_URL from the environment / backend\\.env, exactly like the server, and
records every change in the audit log (user "console"). Passwords are read with a hidden
prompt and never printed.
"""
import argparse
import getpass
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)  # .env and relative sqlite paths resolve like the server

from pydantic import ValidationError  # noqa: E402

from app import crud, models, schemas  # noqa: E402
from app.database import SessionLocal, check_schema_version  # noqa: E402
from app.permissions import audit  # noqa: E402
from app.security import get_password_hash  # noqa: E402

CONSOLE = {"id": None, "username": "console"}
# Roles this console can give an account directly; student accounts use --student instead
ASSIGNABLE_ROLES = models.USER_ROLES + (models.PROFESSOR_ROLE,)


def find(db, username):
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        sys.exit(f"No user named {username!r}")
    return user


def refuse_last_admin(db, user, role, active):
    """Same rule as the API: never leave the system without an active admin."""
    if user.role == "admin" and user.is_active and not (role == "admin" and active) \
            and crud.count_other_active_admins(db, user.id) == 0:
        sys.exit(f"{user.username} is the last active admin account; promote another admin first. Nothing was changed")


def read_new_password() -> str:
    """Prompt twice without echo. Refuses to run without an interactive terminal, so a
    password can never come from a pipe, a file, a command-line argument or shell history.
    Same rule as the API: 8-72 bytes (bcrypt uses at most 72)."""
    if not sys.stdin.isatty():
        sys.exit("Run this command in an interactive terminal: the password is typed at a hidden prompt")
    password = getpass.getpass("New password (8-72 characters, not shown): ")
    if not 8 <= len(password.encode("utf-8")) <= 72:
        sys.exit("The password must be 8-72 characters long; nothing was changed")
    if password != getpass.getpass("Repeat the new password: "):
        sys.exit("The passwords do not match; nothing was changed")
    return password


def main(argv=None):
    parser = argparse.ArgumentParser(description="AttendAI account administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list accounts and roles")
    p = sub.add_parser("set-role", help="change an account's role")
    p.add_argument("username")
    p.add_argument("role", choices=ASSIGNABLE_ROLES)
    p = sub.add_parser("create", help="create an account (prompts for the password)")
    p.add_argument("username")
    p.add_argument("email")
    p.add_argument("--role", choices=ASSIGNABLE_ROLES, default="viewer")
    p.add_argument("--student", metavar="STUDENT_ID",
                   help="create a student account that sees only this student's attendance (e.g. RA2511030040008)")
    p = sub.add_parser("set-password", help="set a new password for an account (hidden prompt)")
    p.add_argument("username")
    for name in ("activate", "deactivate"):
        p = sub.add_parser(name)
        p.add_argument("username")
    args = parser.parse_args(argv)

    schema = check_schema_version()
    if schema["state"] == "outdated":
        sys.exit(f"The database schema is {schema['current']} but {schema['expected']} is required. "
                 f"Back up the database and run `alembic upgrade head` first.")

    db = SessionLocal()
    try:
        if args.command == "list":
            for user in db.query(models.User).order_by(models.User.id):
                state = "active" if user.is_active else "inactive"
                link = f"  student #{user.student_db_id}" if user.student_db_id else ""
                print(f"{user.id:>4}  {user.username:<24} {user.role:<9} {state}{link}")
            return
        if args.command == "set-role":
            user = find(db, args.username)
            if user.role == models.STUDENT_ROLE:
                sys.exit(f"{user.username} is a student account; it cannot be given a staff role. Nothing was changed")
            before = user.role
            refuse_last_admin(db, user, args.role, user.is_active)
            user.role = args.role
            audit(db, CONSOLE, "user.update", "user", user.id, {"username": user.username, "before": {"role": before},
                                                                "after": {"role": args.role}})
            db.commit()
            print(f"{user.username}: {before} -> {args.role}")
        elif args.command == "create":
            try:  # same rules as the API
                fields = schemas.UserCreate(username=args.username, email=args.email, password="x" * 8)
            except ValidationError as error:
                sys.exit("Invalid account details: " + "; ".join(
                    f"{e['loc'][0]} {e['msg']}" for e in error.errors()))
            args.username, args.email = fields.username, fields.email
            if crud.find_conflicting_user(db, username=args.username, email=args.email):
                sys.exit("A user with this username or email already exists (letter case is ignored)")
            student = None
            if args.student:
                if args.role != "viewer":
                    sys.exit("--student creates a student account; leave out --role")
                student = db.query(models.Student).filter(models.Student.student_id == args.student,
                                                          models.Student.person_type == "student",
                                                          models.Student.deleted_at.is_(None)).first()
                if student is None:
                    sys.exit(f"No current student with ID {args.student!r}")
                if db.query(models.User).filter(models.User.student_db_id == student.id).first():
                    sys.exit(f"Student {student.student_id} already has an account")
                args.role = models.STUDENT_ROLE
            password = read_new_password()
            user = models.User(username=args.username, email=args.email, role=args.role, is_active=True,
                               student_db_id=student.id if student else None,
                               hashed_password=get_password_hash(password))
            db.add(user)
            db.flush()
            details = {"username": user.username, "role": args.role}
            if student is not None:
                details["student_id"] = student.student_id
            audit(db, CONSOLE, "user.create", "user", user.id, details)
            db.commit()
            print(f"Created {user.username} ({args.role}{', student ' + student.student_id if student else ''})")
        elif args.command == "set-password":
            user = find(db, args.username)
            password = read_new_password()
            # Only the hash changes; role, active state and every other account stay as they are
            user.hashed_password = get_password_hash(password)
            audit(db, CONSOLE, "user.password_reset", "user", user.id, {"username": user.username})
            db.commit()
            print(f"Password updated for {user.username} (role {user.role}, "
                  f"{'active' if user.is_active else 'INACTIVE - activate the account to sign in'})")
        else:
            user = find(db, args.username)
            refuse_last_admin(db, user, user.role, args.command == "activate")
            user.is_active = args.command == "activate"
            audit(db, CONSOLE, "user.update", "user", user.id, {"username": user.username,
                                                                "after": {"is_active": user.is_active}})
            db.commit()
            print(f"{user.username}: {'activated' if user.is_active else 'deactivated'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
