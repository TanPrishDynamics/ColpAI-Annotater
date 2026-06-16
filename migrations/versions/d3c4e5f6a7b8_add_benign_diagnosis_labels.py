"""add benign diagnosis labels (inflammation, infection, erosion)

Adds three values to every Postgres enum backed by DiagnosisLabel:
diagnosis_label, diagnosis_label_histo, consensus_label, lesion_label.

Postgres has no DROP VALUE, so downgrade is a no-op.

Revision ID: d3c4e5f6a7b8
Revises: c2b3d4e5f6a7
Create Date: 2026-06-15 11:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'd3c4e5f6a7b8'
down_revision = 'c2b3d4e5f6a7'
branch_labels = None
depends_on = None

_ENUM_TYPES = ('diagnosis_label', 'diagnosis_label_histo', 'consensus_label', 'lesion_label')
_NEW_VALUES = ('INFLAMMATION', 'INFECTION', 'EROSION')


def upgrade():
    for enum_type in _ENUM_TYPES:
        for value in _NEW_VALUES:
            op.execute(f"ALTER TYPE {enum_type} ADD VALUE IF NOT EXISTS '{value}'")


def downgrade():
    # Postgres cannot remove enum values; nothing to do.
    pass
