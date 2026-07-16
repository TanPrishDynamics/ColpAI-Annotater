"""CLI: recompute the patient consensus for every patient with multiple submitted diagnoses.

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
            f"Eligible patients: {stats['eligible_patients']} | "
            f"Consensus rows written: {stats['consensus_written']}"
        )


if __name__ == '__main__':
    main()
