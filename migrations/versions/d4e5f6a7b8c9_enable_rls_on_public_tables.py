"""Enable row level security on all public tables

Supabase exposes the ``public`` schema through its auto-generated REST API
(PostgREST), where anyone holding the project's anon key can read and write
tables that don't have RLS. The app never uses that API: it connects straight to
Postgres as the table owner, which bypasses RLS, and it only uses the
service_role key for Storage. So we enable RLS with no policies, which denies
all access through the REST API and changes nothing for the app.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-30 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


TABLES = (
    'alembic_version',
    'users',
    'audit_log',
    'discarded_images',
    'images',
    'regions',
    'review_actions',
    'image_annotations',
    'patient_consensus',
    'patient_diagnoses',
    'diagnosis_review_actions',
)


def upgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')


def downgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY')
