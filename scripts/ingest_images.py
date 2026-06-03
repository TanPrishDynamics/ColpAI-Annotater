"""CLI: scan one or more directories and register images in the DB.

Usage:
    python -m scripts.ingest_images --root "/path/to/dir" --dataset kaggle_v4
    python -m scripts.ingest_images --root . --dataset mixed --dry-run
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from app import create_app
from app.services.ingestion import ingest_directory


@click.command()
@click.option('--root', 'root', required=True, type=click.Path(exists=True, file_okay=False),
              help='Directory to scan recursively.')
@click.option('--dataset', 'dataset_source', required=True,
              help='Logical dataset name to attach to all ingested rows.')
@click.option('--dry-run', is_flag=True, help='Scan and report counts but do not write to DB.')
def main(root: str, dataset_source: str, dry_run: bool):
    app = create_app()
    with app.app_context():
        click.echo(f"Ingesting from: {root}")
        click.echo(f"Dataset label:  {dataset_source}")
        if dry_run:
            click.echo('Mode: DRY RUN (no DB writes)')

        try:
            summary = ingest_directory(
                root=Path(root),
                dataset_source=dataset_source,
                dry_run=dry_run,
            )
        except FileNotFoundError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

        click.echo('')
        click.echo(f"  Scanned:           {summary.scanned}")
        click.echo(f"  Inserted:          {summary.inserted}")
        click.echo(f"  Skipped duplicate: {summary.skipped_duplicate}")
        click.echo(f"  Skipped unreadable:{summary.skipped_unreadable}")
        if summary.errors:
            click.echo(f"\n  First 5 errors:")
            for err in summary.errors[:5]:
                click.echo(f"    - {err}")


if __name__ == '__main__':
    main()
