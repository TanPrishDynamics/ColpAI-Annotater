"""Minimal HTML pages so the browser flow works in Phase 1."""
from flask import Blueprint, render_template, redirect, url_for
from flask_login import current_user, login_required

bp = Blueprint('pages', __name__)


@bp.get('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('pages.dashboard'))
    return redirect(url_for('pages.login_page'))


@bp.get('/login')
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('pages.dashboard'))
    return render_template('login.html')


@bp.get('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)


@bp.get('/annotate')
@login_required
def annotate():
    """Annotation workbench. The page boots without an image id and asks the
    server for the next unannotated image once loaded."""
    if current_user.role.value == 'reviewer':
        return redirect(url_for('pages.review'))
    return render_template('annotate.html', user=current_user)


@bp.get('/annotate/<image_id>')
@login_required
def annotate_image(image_id: str):
    if current_user.role.value == 'reviewer':
        return redirect(url_for('pages.review'))
    return render_template('annotate.html', user=current_user, image_id=image_id)


@bp.get('/review')
@login_required
def review():
    if current_user.role.value not in {'reviewer', 'admin'}:
        return render_template('forbidden.html', user=current_user), 403
    return render_template('review.html', user=current_user)


@bp.get('/admin')
@login_required
def admin():
    if current_user.role.value != 'admin':
        return render_template('forbidden.html', user=current_user), 403
    return render_template('admin.html', user=current_user)
