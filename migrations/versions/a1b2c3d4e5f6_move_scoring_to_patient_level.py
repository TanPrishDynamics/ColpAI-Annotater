"""move reid/swede scoring to patient level; drop per-image diagnosis fields

The FINAL diagnosis (colposcopic_impression, histopathology_result, confidence)
and the Reid/Swede scoring indices are now recorded once per patient per
annotator (see patient_diagnoses), not once per photo. This drops the
now-unused columns from image_annotations; `notes` stays as a general per-image
observation field.

Downgrade re-adds the columns (nullable) but the data is NOT recoverable --
it wasn't preserved anywhere during the drop.

Revision ID: a1b2c3d4e5f6
Revises: f5a6b7c8d9e0
Create Date: 2026-07-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'f5a6b7c8d9e0'
branch_labels = None
depends_on = None

_SCORE_COLUMNS = (
    'reid_margin', 'reid_color', 'reid_vessels', 'reid_iodine',
    'swede_aceto', 'swede_margin', 'swede_vessels', 'swede_size', 'swede_iodine',
)


def _histo_enum():
    if op.get_bind().dialect.name == 'postgresql':
        return postgresql.ENUM(
            'NORMAL', 'CIN1', 'CIN2', 'CIN3', 'AIS', 'INVASIVE_CANCER',
            'INFLAMMATION', 'INFECTION', 'EROSION',
            name='diagnosis_label_histo', create_type=False,
        )
    return sa.Enum(
        'NORMAL', 'CIN1', 'CIN2', 'CIN3', 'AIS', 'INVASIVE_CANCER',
        'INFLAMMATION', 'INFECTION', 'EROSION',
        name='diagnosis_label_histo',
    )


def upgrade():
    with op.batch_alter_table('patient_diagnoses') as batch_op:
        for col in _SCORE_COLUMNS:
            batch_op.add_column(sa.Column(col, sa.Integer(), nullable=True))

    with op.batch_alter_table('image_annotations') as batch_op:
        for col in _SCORE_COLUMNS:
            batch_op.drop_column(col)
        batch_op.drop_column('colposcopic_impression')
        batch_op.drop_column('histopathology_result')
        batch_op.drop_column('confidence')


def downgrade():
    with op.batch_alter_table('image_annotations') as batch_op:
        batch_op.add_column(sa.Column('confidence', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('histopathology_result', _histo_enum(), nullable=True))
        batch_op.add_column(sa.Column('colposcopic_impression', sa.JSON(), nullable=True))
        for col in reversed(_SCORE_COLUMNS):
            batch_op.add_column(sa.Column(col, sa.Integer(), nullable=True))

    with op.batch_alter_table('patient_diagnoses') as batch_op:
        for col in reversed(_SCORE_COLUMNS):
            batch_op.drop_column(col)
