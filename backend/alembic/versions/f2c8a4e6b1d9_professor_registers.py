"""Professor portal: period assignments and submitted attendance registers

Purely additive - no rows are changed or deleted:
- timetable_periods.professor_user_id: NULL for every existing period (not assigned yet).
  An admin assigns a "professor" account to a period on the Timetable page.
- attendance_registers: new, empty table. One row per (period, date) once a professor
  submits that class's register; the per-student decisions are attendance_overrides rows.
The "professor" role needs no schema change (users.role is a free string column).

Revision ID: f2c8a4e6b1d9
Revises: b4d2f6a8c1e3
Create Date: 2026-10-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2c8a4e6b1d9'
down_revision: Union[str, Sequence[str], None] = 'b4d2f6a8c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite cannot ALTER TABLE ADD CONSTRAINT: batch mode recreates the table (rows kept)
    with op.batch_alter_table('timetable_periods') as batch_op:
        batch_op.add_column(sa.Column('professor_user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_timetable_periods_professor_user_id', 'users', ['professor_user_id'], ['id'])
        batch_op.create_index('ix_timetable_periods_professor_user_id', ['professor_user_id'])

    op.create_table(
        'attendance_registers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('first_submitted_by_user_id', sa.Integer(), nullable=False),
        sa.Column('first_submitted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('submitted_by_user_id', sa.Integer(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['period_id'], ['timetable_periods.id']),
        sa.ForeignKeyConstraint(['first_submitted_by_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['submitted_by_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('period_id', 'date', name='uq_register_period_date'),
    )
    op.create_index('ix_attendance_registers_id', 'attendance_registers', ['id'])
    op.create_index('ix_attendance_registers_period_id', 'attendance_registers', ['period_id'])


def downgrade() -> None:
    """Downgrade schema.

    WARNING: removes professor assignments and submitted-register records (the per-student
    decisions in attendance_overrides are kept). Refuses to run while any exist unless forced:
        alembic -x force_data_loss=true downgrade b4d2f6a8c1e3
    (back up the database first). Offline (--sql) generation is refused.
    """
    if context.is_offline_mode():
        raise RuntimeError("Offline (--sql) downgrade of f2c8a4e6b1d9 is not supported: the data-loss check and "
                           "SQLite batch operations need a live database connection.")
    bind = op.get_bind()
    assigned = bind.execute(sa.text("SELECT count(*) FROM timetable_periods WHERE professor_user_id IS NOT NULL")).scalar()
    registers = bind.execute(sa.text("SELECT count(*) FROM attendance_registers")).scalar()
    force = context.get_x_argument(as_dictionary=True).get("force_data_loss", "").lower() in ("1", "true", "yes")
    if (assigned or registers) and not force:
        raise RuntimeError(
            f"Refusing to downgrade f2c8a4e6b1d9: {assigned} professor assignment(s) and {registers} submitted "
            "register(s) would be lost. Back up the database, then rerun with: "
            "alembic -x force_data_loss=true downgrade b4d2f6a8c1e3")
    op.drop_index('ix_attendance_registers_period_id', table_name='attendance_registers')
    op.drop_index('ix_attendance_registers_id', table_name='attendance_registers')
    op.drop_table('attendance_registers')
    with op.batch_alter_table('timetable_periods') as batch_op:
        batch_op.drop_index('ix_timetable_periods_professor_user_id')
        batch_op.drop_constraint('fk_timetable_periods_professor_user_id', type_='foreignkey')
        batch_op.drop_column('professor_user_id')
