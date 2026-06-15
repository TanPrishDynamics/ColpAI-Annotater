"""One-off: move stored objects from ``PAT-NNN/images/<file>`` to ``PAT-NNN/<file>``.

The first bulk upload preserved the on-disk ``images/`` subfolder in the storage
key. This flattens existing rows: it moves each object in the bucket (no
re-upload) and updates ``Image.source_path`` to match.

Usage::

    python -m scripts.flatten_patient_keys --dry-run
    python -m scripts.flatten_patient_keys
"""
from __future__ import annotations

from pathlib import PurePosixPath

import click
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Image
from app.services import storage


@click.command()
@click.option('--dry-run', is_flag=True, help='Show what would move, but change nothing.')
def main(dry_run: bool):
    app = create_app()
    with app.app_context():
        rows = db.session.execute(
            select(Image).where(Image.source_path.like('%/images/%'))
        ).scalars().all()
        click.echo(f"Rows to flatten: {len(rows)}")
        if dry_run:
            click.echo('Mode: DRY RUN (no moves, no DB writes)\n')

        moved = errors = 0
        for img in rows:
            old_key = img.source_path
            filename = PurePosixPath(old_key).name
            prefix = img.patient_code or PurePosixPath(old_key).parts[0]
            new_key = f"{prefix}/{filename}"
            if new_key == old_key:
                continue

            click.echo(f"{old_key}  ->  {new_key}")
            if dry_run:
                continue

            try:
                storage.move_image(old_key, new_key)
            except storage.StorageError as e:
                errors += 1
                click.echo(f"  ! move failed: {e}", err=True)
                continue
            img.source_path = new_key
            moved += 1

        if not dry_run:
            db.session.commit()
            click.echo(f"\nMoved: {moved}   Errors: {errors}")


if __name__ == '__main__':
    main()
