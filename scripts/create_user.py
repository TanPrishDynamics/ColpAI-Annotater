"""CLI: create a user (annotator/reviewer/admin).

Usage:
    python -m scripts.create_user --username dr_smith --role annotator
    python -m scripts.create_user --username admin --role admin --full-name "Site Admin"
"""
from __future__ import annotations

import getpass
import sys

import click

from app import create_app
from app.extensions import db
from app.models import User
from app.models.enums import UserRole


@click.command()
@click.option('--username', required=True)
@click.option('--role', required=True,
              type=click.Choice([r.value for r in UserRole]))
@click.option('--full-name', default=None)
@click.option('--password', default=None,
              help='If omitted, prompts interactively.')
def main(username: str, role: str, full_name: str | None, password: str | None):
    app = create_app()
    with app.app_context():
        existing = db.session.query(User).filter_by(username=username).first()
        if existing:
            click.echo(f"User '{username}' already exists.", err=True)
            sys.exit(1)

        if password is None:
            password = getpass.getpass('Password: ')
            confirm = getpass.getpass('Confirm:  ')
            if password != confirm:
                click.echo('Passwords do not match.', err=True)
                sys.exit(1)
        if not password:
            click.echo('Password cannot be empty.', err=True)
            sys.exit(1)

        user = User(
            username=username,
            role=UserRole(role),
            full_name=full_name,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        click.echo(f"Created user: {user.username} (id={user.id}, role={user.role.value})")


if __name__ == '__main__':
    main()
