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
from sqlalchemy import case, distinct, func, select
from werkzeug.utils import secure_filename

from app.api.errors import error_response
from app.extensions import db
from app.models import ConsensusLabel, DiscardedImage, Image, ImageAnnotation, User
from app.models.enums import AnnotationStatus, UserRole
from app.schemas.admin import UserCreate, UserUpdate
from app.services import storage
from app.services.ingestion import ingest_upload

bp = Blueprint('admin', __name__, url_prefix='/api/v1/admin')

# Bucket folder every upload is grouped under, e.g. "upload/PAT-001/<file>".
UPLOAD_PREFIX = 'upload'


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


def _next_patient_number() -> int:
    """Smallest unused N for a PAT-<NNN> code, based on what's already in the DB."""
    codes = db.session.execute(
        select(Image.patient_code).where(Image.patient_code.isnot(None)).distinct()
    ).scalars().all()
    highest = 0
    for code in codes:
        if code and code.upper().startswith('PAT-'):
            try:
                highest = max(highest, int(code.split('-', 1)[1]))
            except (IndexError, ValueError):
                continue
    return highest + 1


def _split_folder_path(relpath: str):
    """Return (group, subpath) for an uploaded file's relative path.

    A folder upload sends paths like ``selected/PAT_A/img.jpg`` (browser prepends
    the chosen folder). The first sub-directory under the selection identifies the
    patient group; the remainder is the path within that folder. Returns
    ``(None, basename)`` for a plain (non-folder) file.
    """
    parts = [p for p in relpath.replace('\\', '/').split('/') if p not in ('', '.', '..')]
    if len(parts) <= 1:
        return None, (parts[-1] if parts else relpath)
    if len(parts) == 2:
        return parts[0], parts[1]          # selected one patient folder
    return parts[1], '/'.join(parts[2:])    # selected a parent of patient folders


@bp.post('/images/upload')
@login_required
def upload_images():
    """Upload images (or whole folders) into a dataset, ready for annotation.

    Multipart form: ``files`` (one or more) + ``dataset`` (the dataset_source
    label). Each file is sha256-deduped, validated, and stored via the configured
    storage backend.

    Folder uploads: when files carry a relative path (the browser's
    ``webkitRelativePath``, sent as the file's name), each top-level folder is
    treated as one patient and auto-renamed ``PAT-001``, ``PAT-002``, ... . Each
    image is stored flat under ``upload/PAT-NNN/<filename>`` in the bucket (any
    nested sub-dirs such as ``images/`` are dropped).
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

    # Parse each file's path and assign a PAT code per distinct folder group.
    parsed = [(_split_folder_path(f.filename), f) for f in files]
    groups = sorted({grp for (grp, _), _ in parsed if grp})
    start = _next_patient_number()
    code_for_group = {grp: f"PAT-{start + i:03d}" for i, grp in enumerate(groups)}

    upload_dir = Path(current_app.config['UPLOAD_DIR'])
    counts = {'ingested': 0, 'duplicate': 0, 'error': 0}
    results = []
    for (grp, sub), fs in parsed:
        patient_code = code_for_group.get(grp)
        # Store flat under the patient folder: upload/PAT-NNN/<filename>
        # (drop any nested sub-dirs like the on-disk "images/").
        if patient_code:
            filename = secure_filename(Path(sub).name) or 'file'
            object_key = f"{UPLOAD_PREFIX}/{patient_code}/{filename}"
        else:
            object_key = None
        r = ingest_upload(
            fs, dataset, upload_dir,
            patient_code=patient_code, object_key=object_key,
        )
        counts[r.status] = counts.get(r.status, 0) + 1
        results.append({
            'filename': r.filename,
            'patient_code': patient_code,
            'status': r.status,
            'image_id': r.image_id,
            'message': r.message,
        })

    db.session.commit()

    current_app.logger.info("image upload: dataset=%r counts=%s", dataset, counts)
    for r in results:
        if r['status'] != 'ingested':
            current_app.logger.info(
                "  %s [%s] %s", r['filename'], r['status'], r['message'] or ''
            )

    return jsonify({
        'dataset': dataset,
        'counts': counts,
        'patient_codes': sorted(code_for_group.values()),
        'results': results,
    }), 201


def _delete_images(images: list[Image]) -> dict:
    """Delete Image rows and their bucket blobs, keeping storage and DB in sync.

    Annotations cascade via the Image relationship; consensus/discard rows have no
    cascade, so they're removed explicitly first to avoid FK violations. Storage
    deletes are best-effort (a missing blob is fine). Caller commits.
    """
    blobs_deleted = blobs_missing = 0
    for img in images:
        try:
            if storage.delete_image(img.source_path):
                blobs_deleted += 1
            else:
                blobs_missing += 1
        except storage.StorageError as e:
            current_app.logger.warning("delete blob failed for %s: %s", img.source_path, e)
            blobs_missing += 1

        ConsensusLabel.query.filter_by(image_id=img.id).delete(synchronize_session=False)
        DiscardedImage.query.filter_by(image_id=img.id).delete(synchronize_session=False)
        db.session.delete(img)  # annotations + regions cascade

    return {'rows_deleted': len(images), 'blobs_deleted': blobs_deleted,
            'blobs_missing': blobs_missing}


@bp.delete('/images/patients/<patient_code>')
@login_required
def delete_patient(patient_code: str):
    """Delete every image (DB row + bucket object) for one PAT-NNN patient.

    Removes the blobs from storage and the rows from the DB together, so a deleted
    patient stops counting toward PAT numbering. Cascades to annotations/reviews.
    """
    guard = _require_admin()
    if guard is not None:
        return guard

    images = Image.query.filter_by(patient_code=patient_code).all()
    if not images:
        return error_response('not_found', f'No images for {patient_code}.', status=404)

    summary = _delete_images(images)
    db.session.commit()
    current_app.logger.info("deleted patient %s: %s", patient_code, summary)
    return jsonify({'patient_code': patient_code, **summary})


@bp.delete('/images/<image_id>')
@login_required
def delete_image(image_id: str):
    """Delete a single image (DB row + bucket object)."""
    guard = _require_admin()
    if guard is not None:
        return guard

    img = db.session.get(Image, image_id)
    if img is None:
        return error_response('not_found', 'Image not found.', status=404)

    summary = _delete_images([img])
    db.session.commit()
    return jsonify({'image_id': image_id, **summary})


@bp.get('/annotated')
@login_required
def list_annotated():
    """List every reviewer-approved annotation that has a stored final annotated image.

    The final annotated image is only rendered and stored once a reviewer approves
    (status ``reviewed``), so the gallery shows approved work. Each item's image is
    served from the existing ``/api/v1/annotations/<id>/crop`` endpoint (admins may
    read any annotation).
    """
    guard = _require_admin()
    if guard is not None:
        return guard

    rows = (
        db.session.query(ImageAnnotation, Image, User)
        .join(Image, ImageAnnotation.image_id == Image.id)
        .join(User, ImageAnnotation.annotator_id == User.id)
        .filter(
            ImageAnnotation.status == AnnotationStatus.reviewed,
            ImageAnnotation.crop_path.isnot(None),
        )
        .order_by(ImageAnnotation.submitted_at.desc())
        .all()
    )

    items = [{
        'annotation_id': ann.id,
        'image_id': img.id,
        'patient_code': img.patient_code,
        'dataset_source': img.dataset_source,
        'impression': ", ".join(ann.colposcopic_impression) if ann.colposcopic_impression else None,
        'region_count': len(ann.regions),
        'annotator': user.full_name or user.username,
        'submitted_at': ann.submitted_at.isoformat() if ann.submitted_at else None,
        'image_url': f'/api/v1/annotations/{ann.id}/crop',
    } for ann, img, user in rows]

    return jsonify({'items': items, 'count': len(items)})


@bp.get('/patients')
@login_required
def list_patients():
    """Per-patient annotation progress: total images vs. images that have a
    submitted (or better) annotation. Powers the admin patients overview.
    """
    guard = _require_admin()
    if guard is not None:
        return guard

    submitted_like = (
        AnnotationStatus.submitted,
        AnnotationStatus.reviewed,
        AnnotationStatus.consensus,
    )
    annotated_img = case((ImageAnnotation.status.in_(submitted_like), Image.id), else_=None)

    rows = (
        db.session.query(
            Image.patient_code,
            Image.dataset_source,
            func.count(distinct(Image.id)).label('total'),
            func.count(distinct(annotated_img)).label('annotated'),
        )
        .outerjoin(ImageAnnotation, ImageAnnotation.image_id == Image.id)
        .filter(Image.patient_code.isnot(None))
        .group_by(Image.patient_code, Image.dataset_source)
        .order_by(Image.patient_code)
        .all()
    )

    items = []
    summary = {'done': 0, 'partial': 0, 'not_started': 0}
    for code, dataset, total, annotated in rows:
        if annotated == 0:
            status = 'not_started'
        elif annotated >= total:
            status = 'done'
        else:
            status = 'partial'
        summary[status] += 1
        items.append({
            'patient_code': code,
            'dataset_source': dataset,
            'total_images': total,
            'annotated_images': annotated,
            'status': status,
        })

    return jsonify({'items': items, 'count': len(items), 'summary': summary})
