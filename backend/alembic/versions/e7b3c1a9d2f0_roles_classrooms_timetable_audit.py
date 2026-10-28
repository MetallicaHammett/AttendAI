"""Roles, staff, classrooms, timetable, overrides, audit log, camera runs

Purely additive - no existing rows are deleted and no existing column is removed:
- users.role: existing accounts become "operator" (day-to-day use, no user/camera
  configuration). Nobody becomes "admin" automatically: promote an account with
  scripts/manage_users.py. New self-registered accounts are "viewer".
- students.person_type: "student" for every existing row.
- attendance_events.source ("camera" for every existing row), created_by_user_id, note.
- cameras.classroom_id: NULL for every existing camera.
- New tables: classrooms, timetable_periods, holidays, attendance_overrides,
  audit_log, camera_runs.

Revision ID: e7b3c1a9d2f0
Revises: d5a1e7c2b9f4
Create Date: 2026-10-19 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7b3c1a9d2f0'
down_revision: Union[str, Sequence[str], None] = 'd5a1e7c2b9f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = ("camera_runs", "audit_log", "attendance_overrides", "holidays", "timetable_periods", "classrooms")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('role', sa.String(length=20), nullable=False, server_default='viewer'))
    # Existing accounts keep day-to-day access; admin rights are granted explicitly
    op.execute("UPDATE users SET role = 'operator'")

    op.add_column('students', sa.Column('person_type', sa.String(length=20), nullable=False,
                                        server_default='student'))

    op.create_table(
        'classrooms',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('building', sa.String(length=100), nullable=True),
        sa.Column('capacity', sa.Integer(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_classrooms_id'), 'classrooms', ['id'], unique=False)

    # SQLite cannot ALTER TABLE ADD CONSTRAINT: batch mode recreates the table (rows kept)
    with op.batch_alter_table('cameras') as batch_op:
        batch_op.add_column(sa.Column('classroom_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_cameras_classroom_id'), ['classroom_id'], unique=False)
        batch_op.create_foreign_key('fk_cameras_classroom_id', 'classrooms', ['classroom_id'], ['id'])

    with op.batch_alter_table('attendance_events') as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=20), nullable=False, server_default='camera'))
        batch_op.add_column(sa.Column('created_by_user_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('note', sa.Text(), nullable=True))
        batch_op.create_foreign_key('fk_attendance_events_created_by_user_id', 'users', ['created_by_user_id'], ['id'])

    op.create_table(
        'timetable_periods',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('classroom_id', sa.Integer(), nullable=False),
        sa.Column('class_name', sa.String(length=100), nullable=False),
        sa.Column('course_code', sa.String(length=50), nullable=True),
        sa.Column('course_name', sa.String(length=200), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('grace_minutes', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['classroom_id'], ['classrooms.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_timetable_periods_id'), 'timetable_periods', ['id'], unique=False)
    op.create_index(op.f('ix_timetable_periods_classroom_id'), 'timetable_periods', ['classroom_id'], unique=False)
    op.create_index(op.f('ix_timetable_periods_class_name'), 'timetable_periods', ['class_name'], unique=False)

    op.create_table(
        'holidays',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date')
    )
    op.create_index(op.f('ix_holidays_id'), 'holidays', ['id'], unique=False)

    op.create_table(
        'attendance_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('period_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['period_id'], ['timetable_periods.id'], ),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('period_id', 'date', 'student_id', name='uq_override_period_date_student')
    )
    op.create_index(op.f('ix_attendance_overrides_id'), 'attendance_overrides', ['id'], unique=False)
    op.create_index(op.f('ix_attendance_overrides_period_id'), 'attendance_overrides', ['period_id'], unique=False)
    op.create_index(op.f('ix_attendance_overrides_student_id'), 'attendance_overrides', ['student_id'], unique=False)

    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(length=50), nullable=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.String(length=50), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_log_id'), 'audit_log', ['id'], unique=False)
    op.create_index(op.f('ix_audit_log_created_at'), 'audit_log', ['created_at'], unique=False)

    op.create_table(
        'camera_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('camera_id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('feed_kind', sa.String(length=20), nullable=True),
        sa.Column('stop_reason', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_camera_runs_id'), 'camera_runs', ['id'], unique=False)
    op.create_index(op.f('ix_camera_runs_camera_id'), 'camera_runs', ['camera_id'], unique=False)
    op.create_index(op.f('ix_camera_runs_started_at'), 'camera_runs', ['started_at'], unique=False)


def _data_at_risk(bind) -> dict:
    """Rows a downgrade would destroy"""
    queries = {f"{table} rows (table is dropped)": f"SELECT count(*) FROM {table}" for table in NEW_TABLES}
    queries.update({
        "staff records (would become students)": "SELECT count(*) FROM students WHERE person_type != 'student'",
        "manual attendance events (would look like camera events)":
            "SELECT count(*) FROM attendance_events WHERE source != 'camera'",
        "admin/viewer roles (all roles are dropped)": "SELECT count(*) FROM users WHERE role != 'operator'",
        "cameras assigned to classrooms": "SELECT count(*) FROM cameras WHERE classroom_id IS NOT NULL",
    })
    counts = {label: bind.execute(sa.text(sql)).scalar() for label, sql in queries.items()}
    return {label: n for label, n in counts.items() if n}


def downgrade() -> None:
    """Downgrade schema.

    WARNING: drops the new tables and columns. Refuses to run while they hold data
    unless forced with:
        alembic -x force_data_loss=true downgrade d5a1e7c2b9f4
    (back up the database first). Offline (--sql) generation is refused: the data
    check and SQLite batch operations need a live connection.
    """
    if context.is_offline_mode():
        raise RuntimeError(
            "Offline (--sql) downgrade of e7b3c1a9d2f0 is not supported: the data-loss check and "
            "SQLite batch operations need a live database connection. Back up the database and run "
            "the downgrade online instead."
        )

    at_risk = _data_at_risk(op.get_bind())
    force = context.get_x_argument(as_dictionary=True).get("force_data_loss", "").lower() in ("1", "true", "yes")
    if at_risk and not force:
        details = "; ".join(f"{n} {label}" for label, n in at_risk.items())
        raise RuntimeError(
            f"Refusing to downgrade e7b3c1a9d2f0: it would permanently lose data ({details}). "
            "Back up the database, then rerun with: alembic -x force_data_loss=true downgrade d5a1e7c2b9f4"
        )

    for table in NEW_TABLES[:5]:  # everything except classrooms (cameras still reference it)
        op.drop_table(table)
    with op.batch_alter_table('attendance_events') as batch_op:
        batch_op.drop_constraint('fk_attendance_events_created_by_user_id', type_='foreignkey')
        batch_op.drop_column('note')
        batch_op.drop_column('created_by_user_id')
        batch_op.drop_column('source')
    with op.batch_alter_table('cameras') as batch_op:
        batch_op.drop_constraint('fk_cameras_classroom_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_cameras_classroom_id'))
        batch_op.drop_column('classroom_id')
    op.drop_table('classrooms')
    with op.batch_alter_table('students') as batch_op:
        batch_op.drop_column('person_type')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('role')
