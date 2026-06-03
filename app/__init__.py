"""Flask app factory for ColpAI annotation platform."""
from __future__ import annotations

import os

from flask import Flask, jsonify, redirect, request, url_for
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.api import register_blueprints
from app.api.errors import register_error_handlers
from app.config import CONFIG_BY_NAME
from app.extensions import db, migrate, login_manager
from app.views import register_views


@event.listens_for(Engine, 'connect')
def _enable_sqlite_pragmas(dbapi_conn, _):
    """WAL mode + foreign keys for SQLite. No-op on other engines."""
    try:
        cursor = dbapi_conn.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()
    except Exception:
        pass


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get('COLPAI_CONFIG', 'dev')
    config_cls = CONFIG_BY_NAME.get(config_name, CONFIG_BY_NAME['dev'])

    app = Flask(__name__)
    app.config.from_object(config_cls)

    # In production, refuse to start with a missing or throwaway secret key.
    if config_name == 'prod':
        secret = app.config.get('SECRET_KEY')
        if not secret or secret == 'dev-secret-change-me-in-prod':
            raise RuntimeError(
                'COLPAI_SECRET_KEY must be set to a strong random value in production. '
                'Generate one with:  python -c "import secrets; print(secrets.token_hex(32))"'
            )

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    @login_manager.unauthorized_handler
    def _unauthorized():
        if request.path.startswith('/api/'):
            return jsonify({'error': {'code': 'unauthorized', 'message': 'Login required.'}}), 401
        return redirect(url_for('pages.login_page'))

    from app import models  # noqa: F401

    register_blueprints(app)
    register_views(app)
    register_error_handlers(app)

    @app.get('/api/v1/health')
    def health():
        return jsonify({'status': 'ok', 'config': config_name})

    return app
