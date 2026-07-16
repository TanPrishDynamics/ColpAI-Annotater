"""patient level diagnosis

Adds patient_diagnoses + patient_consensus (the FINAL diagnosis is now recorded
per patient_code, not per image) and drops the old per-image consensus_labels
table. No changes to image_annotations -- its diagnosis columns stay as an
optional per-image finding.

`annotation_status` and `diagnosis_label_histo` are Postgres enum types already
created by the initial schema migration; reusing them here on a new table would
re-issue CREATE TYPE and fail with DuplicateObject unless create_type=False is
set on Postgres. SQLite has no real enum type (Enum compiles to a CHECK
constraint per-column) so it needs the plain sa.Enum there instead.

Revision ID: f5a6b7c8d9e0
Revises: b9383f714fc1
Create Date: 2026-07-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f5a6b7c8d9e0'
down_revision = 'b9383f714fc1'
branch_labels = None
depends_on = None


def _existing_enum(name: str, values: tuple[str, ...]):
    """An Enum type that must already exist in the DB (reused across tables)."""
    if op.get_bind().dialect.name == 'postgresql':
        return postgresql.ENUM(*values, name=name, create_type=False)
    return sa.Enum(*values, name=name)


def upgrade():
    annotation_status = _existing_enum(
        'annotation_status', ('draft', 'submitted', 'reviewed', 'consensus', 'superseded')
    )
    diagnosis_label_histo = _existing_enum(
        'diagnosis_label_histo',
        ('NORMAL', 'CIN1', 'CIN2', 'CIN3', 'AIS', 'INVASIVE_CANCER', 'INFLAMMATION', 'INFECTION', 'EROSION'),
    )

    op.create_table(
        'patient_diagnoses',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('patient_code', sa.String(length=32), nullable=False),
        sa.Column('annotator_id', sa.String(length=36), nullable=False),
        sa.Column('status', annotation_status, nullable=False),
        sa.Column('colposcopic_impression', sa.JSON(), nullable=True),
        sa.Column('histopathology_result', diagnosis_label_histo, nullable=True),
        sa.Column('confidence', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['annotator_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('patient_code', 'annotator_id', name='uq_patient_diagnosis_annotator'),
    )
    with op.batch_alter_table('patient_diagnoses', schema=None) as batch_op:
        batch_op.create_index('ix_patient_diagnosis_code_status', ['patient_code', 'status'])
        batch_op.create_index(batch_op.f('ix_patient_diagnoses_patient_code'), ['patient_code'])
        batch_op.create_index(batch_op.f('ix_patient_diagnoses_annotator_id'), ['annotator_id'])

    op.create_table(
        'patient_consensus',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('patient_code', sa.String(length=32), nullable=False),
        sa.Column('label', sa.JSON(), nullable=False),
        sa.Column('derived_from', sa.JSON(), nullable=False),
        sa.Column('agreement_score', sa.Float(), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('patient_code'),
    )

    op.drop_table('consensus_labels')


def downgrade():
    op.create_table(
        'consensus_labels',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('image_id', sa.String(length=36), nullable=False),
        sa.Column('label', sa.JSON(), nullable=False),
        sa.Column('derived_from', sa.JSON(), nullable=False),
        sa.Column('agreement_score', sa.Float(), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['image_id'], ['images.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('image_id'),
    )

    op.drop_table('patient_consensus')

    with op.batch_alter_table('patient_diagnoses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_patient_diagnoses_annotator_id'))
        batch_op.drop_index(batch_op.f('ix_patient_diagnoses_patient_code'))
        batch_op.drop_index('ix_patient_diagnosis_code_status')
    op.drop_table('patient_diagnoses')
