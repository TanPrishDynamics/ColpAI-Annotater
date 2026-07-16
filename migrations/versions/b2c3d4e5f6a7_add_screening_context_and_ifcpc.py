"""add screening context (cytology/HPV/management) + per-image IFCPC assessment

Patient level (patient_diagnoses): cytology_result, hpv_status,
management_recommendation, biopsy_taken -- the cervical-screening context and
clinical outcome that surround the colposcopy.

Image level (image_annotations): ifcpc_grade, colposcopy_adequacy -- the IFCPC
2011 per-view summary for a single photo.

All five enum types are brand new (not reused), so they are CREATE TYPE'd here on
Postgres and dropped on downgrade. On SQLite an Enum is just an inline CHECK, so
the explicit create/drop are Postgres-only.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

_ENUMS = {
    'cytology_result': ('NILM', 'ASC_US', 'LSIL', 'ASC_H', 'HSIL', 'AGC', 'AIS', 'SCC', 'unsatisfactory'),
    'hpv_status': ('negative', 'positive_16_18', 'positive_other_hr', 'positive_unknown', 'not_done'),
    'management_recommendation': ('routine_recall', 'repeat_cytology', 'colposcopy_followup',
                                  'biopsy', 'excision_leep', 'refer_oncology'),
    'ifcpc_grade': ('normal', 'minor', 'major', 'suspicious_invasion', 'miscellaneous'),
    'colposcopy_adequacy': ('adequate', 'inadequate'),
}


def _col_type(name: str):
    """Enum column type: on Postgres reference the pre-created type (create_type=False
    so add_column doesn't re-issue CREATE TYPE); on SQLite an inline Enum/CHECK."""
    values = _ENUMS[name]
    if op.get_bind().dialect.name == 'postgresql':
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        for name, values in _ENUMS.items():
            postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    with op.batch_alter_table('patient_diagnoses') as batch_op:
        batch_op.add_column(sa.Column('cytology_result', _col_type('cytology_result'), nullable=True))
        batch_op.add_column(sa.Column('hpv_status', _col_type('hpv_status'), nullable=True))
        batch_op.add_column(sa.Column('management_recommendation', _col_type('management_recommendation'), nullable=True))
        batch_op.add_column(sa.Column('biopsy_taken', sa.Boolean(), nullable=True))

    with op.batch_alter_table('image_annotations') as batch_op:
        batch_op.add_column(sa.Column('ifcpc_grade', _col_type('ifcpc_grade'), nullable=True))
        batch_op.add_column(sa.Column('colposcopy_adequacy', _col_type('colposcopy_adequacy'), nullable=True))


def downgrade():
    with op.batch_alter_table('image_annotations') as batch_op:
        batch_op.drop_column('colposcopy_adequacy')
        batch_op.drop_column('ifcpc_grade')

    with op.batch_alter_table('patient_diagnoses') as batch_op:
        batch_op.drop_column('biopsy_taken')
        batch_op.drop_column('management_recommendation')
        batch_op.drop_column('hpv_status')
        batch_op.drop_column('cytology_result')

    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        for name, values in _ENUMS.items():
            postgresql.ENUM(*values, name=name).drop(bind, checkfirst=True)
