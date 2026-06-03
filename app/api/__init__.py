"""API blueprint registration."""
from app.api.admin import bp as admin_bp
from app.api.annotations import bp as annotations_bp
from app.api.auth import bp as auth_bp
from app.api.dashboard import bp as dashboard_bp
from app.api.export import bp as export_bp
from app.api.images import bp as images_bp
from app.api.regions import annotation_regions_bp, regions_bp
from app.api.review import bp as review_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(images_bp)
    app.register_blueprint(annotations_bp)
    app.register_blueprint(annotation_regions_bp)
    app.register_blueprint(regions_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(admin_bp)
