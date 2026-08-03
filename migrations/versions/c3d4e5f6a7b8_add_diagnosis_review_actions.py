"""Add diagnosis review actions table

Adds a table to track reviewer approval/rejection of patient-level diagnoses,
similar to the existing image annotation review_actions table.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def _existing_enum(name: str, values: tuple[str, ...]):
    """An Enum type that must already exist in the DB (reused across tables)."""
    if op.get_bind().dialect.name == 'postgresql':
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name)


def upgrade():
    review_action = _existing_enum(
        'review_action', ('approve', 'reject', 'edit')
    )

    op.create_table(
        'diagnosis_review_actions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('patient_code', sa.String(length=32), nullable=False),
        sa.Column('patient_diagnosis_id', sa.String(length=36), nullable=False),
        sa.Column('reviewer_id', sa.String(length=36), nullable=False),
        sa.Column('action', review_action, nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['patient_diagnosis_id'], ['patient_diagnoses.id']),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('diagnosis_review_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_diagnosis_review_actions_patient_code'), ['patient_code'])
        batch_op.create_index(batch_op.f('ix_diagnosis_review_actions_patient_diagnosis_id'), ['patient_diagnosis_id'])
        batch_op.create_index(batch_op.f('ix_diagnosis_review_actions_reviewer_id'), ['reviewer_id'])


def downgrade():
    op.drop_table('diagnosis_review_actions')
