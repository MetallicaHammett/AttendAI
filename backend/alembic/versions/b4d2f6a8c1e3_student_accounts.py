"""Student accounts: link an account to the student whose attendance it may see

Purely additive - no rows are changed or deleted:
- users.student_db_id: NULL for every existing account (all staff stay exactly as they are).
  Set only on accounts with role "student", which an admin creates for one student record.

Revision ID: b4d2f6a8c1e3
Revises: e7b3c1a9d2f0
Create Date: 2026-10-20 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d2f6a8c1e3'
down_revision: Union[str, Sequence[str], None] = 'e7b3c1a9d2f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite cannot ALTER TABLE ADD CONSTRAINT: batch mode recreates the table (rows kept)
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('student_db_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_users_student_db_id', 'students', ['student_db_id'], ['id'])
        batch_op.create_unique_constraint('uq_users_student_db_id', ['student_db_id'])


def downgrade() -> None:
    """Downgrade schema.

    WARNING: removes the student links. Refuses to run while any account is linked (or
    has the student role) unless forced with:
        alembic -x force_data_loss=true downgrade e7b3c1a9d2f0
    (back up the database first). Offline (--sql) generation is refused.
    """
    if context.is_offline_mode():
        raise RuntimeError("Offline (--sql) downgrade of b4d2f6a8c1e3 is not supported: the data-loss check and "
                           "SQLite batch operations need a live database connection.")
    linked = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM users WHERE student_db_id IS NOT NULL OR role = 'student'")).scalar()
    force = context.get_x_argument(as_dictionary=True).get("force_data_loss", "").lower() in ("1", "true", "yes")
    if linked and not force:
        raise RuntimeError(
            f"Refusing to downgrade b4d2f6a8c1e3: {linked} student account(s) would lose their link. Back up the "
            "database, then rerun with: alembic -x force_data_loss=true downgrade e7b3c1a9d2f0")
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_constraint('uq_users_student_db_id', type_='unique')
        batch_op.drop_constraint('fk_users_student_db_id', type_='foreignkey')
        batch_op.drop_column('student_db_id')
