"""CLI: bulk-upload a tree of patient folders, one PAT-NNN per subfolder.

Mirrors the admin folder-upload endpoint, but reads straight off disk so a large
local dataset can be ingested in one go (and resumed safely -- sha256 dedup makes
re-runs idempotent).

Expected layout (each immediate subdirectory is one patient)::

    <root>/
        101/ images/ *.png
        106/ images/ *.png
        ...

Each subfolder is assigned the next free ``PAT-NNN`` code (continuing from what is
already in the DB) and its images are stored under ``PAT-NNN/<relpath>`` in the
bucket, exactly like the browser upload.

Usage::

    python -m scripts.bulk_upload_patients --root "C:/.../New ColpoScope" --dataset ColpAI
    python -m scripts.bulk_upload_patients --root "..." --dataset ColpAI --dry-run
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

import click
from werkzeug.datastructures import FileStorage

from app import create_app
from app.extensions import db
from app.models import Image
from app.services.ingestion import DEFAULT_EXTENSIONS, ingest_upload
from sqlalchemy import select


def _next_patient_number() -> int:
    """Smallest unused N for a PAT-<NNN> code, based on what's already in the DB."""
    codes = db.session.execute(
        select(Image.patient_code).where(Image.patient_code.isnot(None)).distinct()
    ).scalars().all()
    highest = 0
    for code in codes:
        if code and code.upper().startswith('PAT-'):
            try:
                highest = max(highest, int(code.split('-', 1)[1]))
            except (IndexError, ValueError):
                continue
    return highest + 1


def _folder_sort_key(p: Path):
    """Numeric order when the folder name is a number, else lexical."""
    return (0, int(p.name)) if p.name.isdigit() else (1, p.name.lower())


def _images_in(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.rglob('*')
         if p.is_file()
         and not p.name.startswith('._')
         and p.suffix.lower() in DEFAULT_EXTENSIONS),
        key=lambda p: p.as_posix().lower(),
    )


@click.command()
@click.option('--root', 'root', required=True, type=click.Path(exists=True, file_okay=False),
              help='Parent folder whose immediate subdirectories are patients.')
@click.option('--dataset', 'dataset_source', required=True,
              help='Logical dataset name attached to every ingested row.')
@click.option('--prefix', 'prefix', default='upload',
              help='Bucket folder to place patients under (default: "upload").')
@click.option('--dry-run', is_flag=True, help='Report the plan but do not upload or write to DB.')
def main(root: str, dataset_source: str, prefix: str, dry_run: bool):
    root_path = Path(root)
    patient_dirs = sorted(
        (d for d in root_path.iterdir() if d.is_dir()),
        key=_folder_sort_key,
    )
    if not patient_dirs:
        click.echo(f"No subfolders found under {root_path}", err=True)
        raise SystemExit(1)

    prefix = prefix.strip('/')

    app = create_app()
    with app.app_context():
        backend = (app.config.get('STORAGE_BACKEND') or 'local').strip().lower()
        upload_dir = Path(app.config['UPLOAD_DIR'])
        start = _next_patient_number()

        # Only folders that actually contain images get a PAT code, so numbering
        # stays contiguous (folders with just videos are skipped).
        scanned = [(d, _images_in(d)) for d in patient_dirs]
        skipped = [d.name for d, imgs in scanned if not imgs]
        with_images = [(d, imgs) for d, imgs in scanned if imgs]

        click.echo(f"Root:     {root_path}")
        click.echo(f"Dataset:  {dataset_source}")
        click.echo(f"Backend:  {backend}")
        click.echo(f"Patients: {len(with_images)} (starting at PAT-{start:03d})")
        if skipped:
            click.echo(f"Skipping (no images): {', '.join(skipped)}")
        if dry_run:
            click.echo('Mode:     DRY RUN (no uploads, no DB writes)')
        click.echo('')

        totals = {'ingested': 0, 'duplicate': 0, 'error': 0}
        for i, (pdir, images) in enumerate(with_images):
            code = f"PAT-{start + i:03d}"
            click.echo(f"{code}  <- {pdir.name}  ({len(images)} images)")
            if dry_run:
                continue

            for img in images:
                # Store flat under the patient folder: <prefix>/PAT-NNN/<filename>
                # (drop any on-disk "images/" subdir).
                object_key = f"{prefix}/{code}/{img.name}" if prefix else f"{code}/{img.name}"
                ctype = mimetypes.guess_type(img.name)[0]
                with img.open('rb') as fh:
                    fs = FileStorage(stream=fh, filename=img.name, content_type=ctype)
                    r = ingest_upload(
                        fs, dataset_source, upload_dir,
                        patient_code=code, object_key=object_key,
                    )
                totals[r.status] = totals.get(r.status, 0) + 1
                if r.status == 'error':
                    click.echo(f"    ! {img.name}: {r.message}", err=True)

            # Commit per patient so a long run is resumable if interrupted.
            db.session.commit()

        click.echo('')
        click.echo(f"  Ingested:  {totals['ingested']}")
        click.echo(f"  Duplicate: {totals['duplicate']}")
        click.echo(f"  Errors:    {totals['error']}")


if __name__ == '__main__':
    main()
