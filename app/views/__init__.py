"""HTML page blueprints (placeholder UI for Phase 1)."""
from app.views.pages import bp as pages_bp


def register_views(app):
    app.register_blueprint(pages_bp)
