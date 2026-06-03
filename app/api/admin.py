"""Admin user-management API. Restricted to users with role `admin`.

- GET   /api/v1/admin/users           - list users with per-doctor progress
- POST  /api/v1/admin/users           - create a doctor/reviewer/admin login
- PATCH /api/v1/admin/users/{id}       - change role, rename, disable, reset password

Disabling a user (is_active=false) blocks login immediately (auth checks it) but
keeps all their existing annotations intact.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func, select

from app.api.errors import error_response
from app.extensions import db
from app.models import ImageAnnotation, User
from app.models.enums import AnnotationStatus, UserRole
from app.schemas.admin import UserCreate, UserUpdate
from app.services.ingestion import ingest_upload

bp = Blueprint('admin', __name__, url_prefix='/api/v1/admin')


def _require_admin():
    if current_user.role.value != UserRole.admin.value:
        return error_response('forbidden', 'Admin role required.', status=403)
    return None


def _progress_by_user() -> dict[str, dict]:
    """Per-annotator counts: submitted / reviewed / drafts, and distinct images."""
    rows = db.session.execute(
        select(
            ImageAnnotation.annotator_id,
            ImageAnnotation.status,
            func.count(ImageAnnotation.id),
        ).group_by(ImageAnnotation.annotator_id, ImageAnnotation.status)
    ).all()

    images = db.session.execute(
        select(
            ImageAnnotation.annotator_id,
            func.count(func.distinct(ImageAnnotation.image_id)),
        ).group_by(ImageAnnotation.annotator_id)
    ).all()
    image_counts = {uid: n for uid, n in images}

    out: dict[str, dict] = {}
    for uid, status, count in rows:
        d = out.setdefault(uid, {'submitted': 0, 'reviewed': 0, 'drafts': 0, 'images': 0})
        if status == AnnotationStatus.submitted:
            d['submitted'] += count
        elif status in (AnnotationStatus.reviewed, AnnotationStatus.consensus):
            d['reviewed'] += count
        elif status == AnnotationStatus.draft:
            d['drafts'] += count
    for uid, d in out.items():
        d['images'] = image_counts.get(uid, 0)
    return out


def _user_dict(user: User, progress: dict) -> dict:
    p = progress.get(user.id, {'submitted': 0, 'reviewed': 0, 'drafts': 0, 'images': 0})
    return {
        'id': user.id,
        'username': user.username,
        'full_name': user.full_name,
        'role': user.role.value,
        'is_active': user.is_active,
        'last_login': user.last_login.isoformat() if user.last_login else None,
        'created_at': user.created_at.isoformat() if user.created_at else None,
        'progress': p,
    }


@bp.get('/users')
@login_required
def list_users():
    guard = _require_admin()
    if guard is not None:
        return guard
    progress = _progress_by_user()
    users = db.session.execute(select(User).order_by(User.username)).scalars().all()
    return jsonify({'items': [_user_dict(u, progress) for u in users]})


@bp.post('/users')
@login_required
def create_user():
    guard = _require_admin()
    if guard is not None:
        return guard

    payload = UserCreate.model_validate(request.get_json(silent=True) or {})
    existing = db.session.query(User.id).filter_by(username=payload.username).first()
    if existing:
        return error_response('username_taken', f"User '{payload.username}' already exists.", status=409)

    user = User(username=payload.username, role=payload.role, full_name=payload.full_name)
    user.set_password(payload.password)
    db.session.add(user)
    db.session.commit()
    return jsonify(_user_dict(user, {})), 201


@bp.patch('/users/<user_id>')
@login_required
def update_user(user_id: str):
    guard = _require_admin()
    if guard is not None:
        return guard

    user = db.session.get(User, user_id)
    if user is None:
        return error_response('not_found', 'User not found.', status=404)

    payload = UserUpdate.model_validate(request.get_json(silent=True) or {})

    # Guard against locking yourself out / demoting the last admin.
    if user.id == current_user.id:
        if payload.is_active is False:
            return error_response('self_disable', 'You cannot disable your own account.', status=409)
        if payload.role is not None and payload.role != UserRole.admin:
            return error_response('self_demote', 'You cannot remove your own admin role.', status=409)

    if payload.role is not None:
        user.role = payload.role
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.is_active is not None:
        user.is_active_flag = payload.is_active
    if payload.password is not None:
        user.set_password(payload.password)

    db.session.commit()
    return jsonify(_user_dict(user, _progress_by_user()))


@bp.post('/images/upload')
@login_required
def upload_images():
    """Upload one or more images into a dataset, ready for annotation.

    Multipart form: ``files`` (one or more) + ``dataset`` (the dataset_source
    label). Each file is sha256-deduped, validated, and stored under UPLOAD_DIR.
    """
    guard = _require_admin()
    if guard is not None:
        return guard

    dataset = (request.form.get('dataset') or '').strip()
    if not dataset:
        return error_response('missing_dataset', 'A dataset name is required.', status=422)

    files = [f for f in request.files.getlist('files') if f and f.filename]
    if not files:
        return error_response('no_files', 'No files were uploaded.', status=422)

    upload_dir = Path(current_app.config['UPLOAD_DIR'])
    counts = {'ingested': 0, 'duplicate': 0, 'error': 0}
    results = []
    for fs in files:
        r = ingest_upload(fs, dataset, upload_dir)
        counts[r.status] = counts.get(r.status, 0) + 1
        results.append({
            'filename': r.filename,
            'status': r.status,
            'image_id': r.image_id,
            'message': r.message,
        })

    db.session.commit()
    return jsonify({'dataset': dataset, 'counts': counts, 'results': results}), 201
