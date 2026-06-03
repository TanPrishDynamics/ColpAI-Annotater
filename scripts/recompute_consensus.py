"""CLI: recompute the ConsensusLabel for every image with multiple submissions.

Usage:
    python -m scripts.recompute_consensus
"""
from __future__ import annotations

import click

from app import create_app
from app.services import consensus


@click.command()
def main():
    app = create_app()
    with app.app_context():
        stats = consensus.recompute_all()
        click.echo(
            f"Eligible images: {stats['eligible_images']} | "
            f"Consensus rows written: {stats['consensus_written']}"
        )


if __name__ == '__main__':
    main()
