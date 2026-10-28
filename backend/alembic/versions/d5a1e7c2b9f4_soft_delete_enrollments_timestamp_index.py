"""Soft delete for students/cameras, course enrollments table, attendance timestamp index

Purely additive - no existing rows or columns are modified or removed:
- students.deleted_at, cameras.deleted_at: nullable, NULL for every existing row
  (= not deleted). DELETE endpoints now set these instead of removing rows, so
  attendance history is preserved.
- enrollments: new table backing /enrollment (previously an in-memory list).
- ix_attendance_events_timestamp: index for dashboard/recent-attendance date queries.

Revision ID: d5a1e7c2b9f4
Revises: c375dca0cd24
Create Date: 2026-10-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5a1e7c2b9f4'
down_revision: Union[str, Sequence[str], None] = 'c375dca0cd24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('students', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('cameras', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'enrollments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('course_code', sa.String(length=50), nullable=False),
        sa.Column('course_name', sa.String(length=200), nullable=False),
        sa.Column('semester', sa.String(length=50), nullable=False),
        sa.Column('section', sa.String(length=20), nullable=True),
        sa.Column('enrolled_date', sa.Date(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['student_id'], ['students.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_enrollments_id'), 'enrollments', ['id'], unique=False)
    op.create_index(op.f('ix_enrollments_student_id'), 'enrollments', ['student_id'], unique=False)

    op.create_index(op.f('ix_attendance_events_timestamp'), 'attendance_events', ['timestamp'], unique=False)


def _data_at_risk(bind) -> dict:
    """Rows a downgrade would destroy or silently un-delete"""
    queries = {
        "enrollments rows (table is dropped)": "SELECT count(*) FROM enrollments",
        "soft-deleted students (would reappear in lists)": "SELECT count(*) FROM students WHERE deleted_at IS NOT NULL",
        "soft-deleted cameras (would reappear in lists)": "SELECT count(*) FROM cameras WHERE deleted_at IS NOT NULL",
    }
    counts = {label: bind.execute(sa.text(sql)).scalar() for label, sql in queries.items()}
    return {label: n for label, n in counts.items() if n}


def downgrade() -> None:
    """Downgrade schema.

    WARNING: drops the enrollments table and the deleted_at markers. Soft-deleted
    students/cameras would reappear as normal rows (still is_active = False).
    Refuses to run while such data exists unless forced with:
        alembic -x force_data_loss=true downgrade c375dca0cd24
    (back up the database first).

    Offline (--sql) generation is refused outright, even when forced: the data
    check needs a live connection, and SQLite batch drop_column needs to reflect
    the table, so Alembic would emit DROP TABLE enrollments and then fail,
    leaving a partial, unguarded script.
    """
    if context.is_offline_mode():
        raise RuntimeError(
            "Offline (--sql) downgrade of d5a1e7c2b9f4 is not supported: the data-loss check and "
            "SQLite batch operations need a live database connection. Back up the database and run "
            "the downgrade online instead."
        )

    at_risk = _data_at_risk(op.get_bind())
    force = context.get_x_argument(as_dictionary=True).get("force_data_loss", "").lower() in ("1", "true", "yes")
    if at_risk and not force:
        details = "; ".join(f"{n} {label}" for label, n in at_risk.items())
        raise RuntimeError(
            f"Refusing to downgrade d5a1e7c2b9f4: it would permanently lose data ({details}). "
            "Back up the database, then rerun with: alembic -x force_data_loss=true downgrade c375dca0cd24"
        )

    op.drop_index(op.f('ix_attendance_events_timestamp'), table_name='attendance_events')
    op.drop_index(op.f('ix_enrollments_student_id'), table_name='enrollments')
    op.drop_index(op.f('ix_enrollments_id'), table_name='enrollments')
    op.drop_table('enrollments')
    with op.batch_alter_table('cameras') as batch_op:
        batch_op.drop_column('deleted_at')
    with op.batch_alter_table('students') as batch_op:
        batch_op.drop_column('deleted_at')
