"""add patient_code to images

Revision ID: c2b3d4e5f6a7
Revises: b1a2c3d4e5f6
Create Date: 2026-06-12 00:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2b3d4e5f6a7'
down_revision = 'b1a2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('images') as batch_op:
        batch_op.add_column(sa.Column('patient_code', sa.String(length=32), nullable=True))
        batch_op.create_index('ix_images_patient_code', ['patient_code'])


def downgrade():
    with op.batch_alter_table('images') as batch_op:
        batch_op.drop_index('ix_images_patient_code')
        batch_op.drop_column('patient_code')
