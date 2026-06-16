"""add Reid and Swede colposcopic scoring columns

Nine 0/1/2 sub-criteria on image_annotations:
  Reid:  reid_margin, reid_color, reid_vessels, reid_iodine
  Swede: swede_aceto, swede_margin, swede_vessels, swede_size, swede_iodine
Totals are derived in the model, not stored.

Revision ID: e4d5f6a7b8c9
Revises: d3c4e5f6a7b8
Create Date: 2026-06-15 11:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4d5f6a7b8c9'
down_revision = 'd3c4e5f6a7b8'
branch_labels = None
depends_on = None

_COLUMNS = (
    'reid_margin', 'reid_color', 'reid_vessels', 'reid_iodine',
    'swede_aceto', 'swede_margin', 'swede_vessels', 'swede_size', 'swede_iodine',
)


def upgrade():
    with op.batch_alter_table('image_annotations') as batch_op:
        for col in _COLUMNS:
            batch_op.add_column(sa.Column(col, sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('image_annotations') as batch_op:
        for col in reversed(_COLUMNS):
            batch_op.drop_column(col)
