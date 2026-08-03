"""Minimal HTML pages so the browser flow works in Phase 1."""
from flask import Blueprint, render_template, redirect, request, url_for
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


@bp.get('/patients')
@login_required
def patients_index():
    """Patient picker. The annotate workbench is patient-first: annotators start
    here, then work through one patient's images at a time."""
    if current_user.role.value == 'reviewer':
        return redirect(url_for('pages.review'))
    return render_template('patients_index.html', user=current_user)


@bp.get('/annotate')
@login_required
def annotate():
    """Annotation workbench. Requires ?patient=CODE -- without one, send the
    annotator to the patient picker rather than mixing images across patients."""
    if current_user.role.value == 'reviewer':
        return redirect(url_for('pages.review'))
    if not request.args.get('patient'):
        return redirect(url_for('pages.patients_index'))
    return render_template('annotate.html', user=current_user)


@bp.get('/annotate/<image_id>')
@login_required
def annotate_image(image_id: str):
    if current_user.role.value == 'reviewer':
        return redirect(url_for('pages.review'))
    return render_template('annotate.html', user=current_user, image_id=image_id)


@bp.get('/patients/<patient_code>/diagnose')
@login_required
def patient_diagnose(patient_code: str):
    if current_user.role.value == 'reviewer':
        return redirect(url_for('pages.review'))
    return render_template('patient_diagnose.html', user=current_user, patient_code=patient_code)


@bp.get('/review')
@login_required
def review():
    if current_user.role.value not in {'reviewer', 'admin'}:
        return render_template('forbidden.html', user=current_user), 403
    return render_template('review.html', user=current_user)


@bp.get('/review/diagnoses')
@login_required
def diagnosis_review():
    if current_user.role.value not in {'reviewer', 'admin'}:
        return render_template('forbidden.html', user=current_user), 403
    return render_template('diagnosis_review.html', user=current_user)


@bp.get('/admin')
@login_required
def admin():
    if current_user.role.value != 'admin':
        return render_template('forbidden.html', user=current_user), 403
    return render_template('admin.html', user=current_user)
