"""Reconcile the images table with what actually exists in storage.

When someone deletes objects/folders straight from the Supabase bucket, the DB
rows are left behind -- they still count toward PAT-NNN numbering and show as
broken images. This script finds every Image whose blob no longer exists and
(optionally) deletes the stale rows so the DB matches the bucket again.

Deleting an Image cascades to its annotations, so rows that already have
annotations are reported and SKIPPED unless --force is given.

Usage::

    python -m scripts.reconcile_storage              # dry run: just report
    python -m scripts.reconcile_storage --apply       # delete stale rows (skips annotated)
    python -m scripts.reconcile_storage --apply --force   # also delete annotated rows
"""
from __future__ import annotations

import click
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Image
from app.services import storage


@click.command()
@click.option('--apply', 'apply', is_flag=True, help='Delete stale rows (default: report only).')
@click.option('--force', is_flag=True, help='Also delete rows that have annotations.')
def main(apply: bool, force: bool):
    app = create_app()
    with app.app_context():
        rows = db.session.execute(select(Image)).scalars().all()
        stale = [img for img in rows if not storage.image_exists(img.source_path)]

        click.echo(f"Scanned {len(rows)} images; {len(stale)} have no blob in storage.")
        if not stale:
            return

        deleted = skipped = 0
        for img in stale:
            n_ann = img.annotations.count()
            tag = f" ({n_ann} annotations)" if n_ann else ""
            if n_ann and not force:
                click.echo(f"  SKIP  {img.patient_code} {img.source_path}{tag}")
                skipped += 1
                continue
            click.echo(f"  {'DEL ' if apply else 'WOULD DEL'} {img.patient_code} {img.source_path}{tag}")
            if apply:
                db.session.delete(img)
                deleted += 1

        if apply:
            db.session.commit()
            click.echo(f"\nDeleted: {deleted}   Skipped (annotated): {skipped}")
        else:
            click.echo("\nDry run -- nothing deleted. Re-run with --apply to delete.")


if __name__ == '__main__':
    main()
