"""add crop_box and crop_path to image_annotations

Revision ID: b1a2c3d4e5f6
Revises: 43c61153cc8c
Create Date: 2026-06-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1a2c3d4e5f6'
down_revision = '43c61153cc8c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('image_annotations') as batch_op:
        batch_op.add_column(sa.Column('crop_box', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('crop_path', sa.String(length=1024), nullable=True))


def downgrade():
    with op.batch_alter_table('image_annotations') as batch_op:
        batch_op.drop_column('crop_path')
        batch_op.drop_column('crop_box')
