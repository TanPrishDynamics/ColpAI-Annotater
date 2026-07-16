"""Admin user-management API. Restricted to users with role `admin`.

- GET   /api/v1/admin/users           - list users with per-doctor progress
- POST  /api/v1/admin/users           - create a doctor/reviewer/admin login
- PATCH /api/v1/admin/users/{id}       - change role, rename, disable, reset password

Disabling a user (is_active=false) blocks login immediately (auth checks it) but
keeps all their existing annotations intact.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import io
import os
from PIL import Image as PILImage

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import case, distinct, func, select
from werkzeug.utils import secure_filename

from app.api.errors import error_response
from app.extensions import db
from app.models import DiscardedImage, Image, ImageAnnotation, PatientConsensus, PatientDiagnosis, User
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
    PatientDiagnosis.query.filter_by(patient_code=patient_code).delete(synchronize_session=False)
    PatientConsensus.query.filter_by(patient_code=patient_code).delete(synchronize_session=False)
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

# Every admin crop is written as a fixed square tile so the training set is uniform.
TRAINING_CROP_SIZE = 512


@bp.post('/images/<image_id>/crop')
@login_required
def crop_image(image_id: str):
    """Crop the original image in place and delete existing annotations.

    The crop is standardized to a fixed ``TRAINING_CROP_SIZE`` x ``TRAINING_CROP_SIZE``
    square (1:1, then resized) so every stored image has identical dimensions for
    model training.
    """
    guard = _require_admin()
    if guard is not None:
        return guard

    img = db.session.get(Image, image_id)
    if img is None:
        return error_response('not_found', 'Image not found.', status=404)

    payload = request.get_json()
    if not payload:
        return error_response('invalid_request', 'Missing JSON body.', status=400)
    
    try:
        x = int(payload['x'])
        y = int(payload['y'])
        w = int(payload['width'])
        h = int(payload['height'])
    except (KeyError, TypeError, ValueError):
        return error_response('invalid_request', 'Invalid crop coordinates.', status=400)

    if w <= 0 or h <= 0:
        return error_response('invalid_request', 'Crop dimensions must be positive.', status=400)

    # Standardize every crop to a square (1:1). The UI already locks the aspect
    # ratio, but force it here too so the stored training image is always NxN
    # regardless of client rounding. Anchored at the top-left of the selection.
    side = min(w, h)
    w = h = side

    try:
        with storage.open_image(img.source_path) as fh:
            with PILImage.open(fh) as pil_img:
                pil_img.load()
                # Clamp coordinates to actual image bounds
                left = max(0, min(x, pil_img.width))
                top = max(0, min(y, pil_img.height))
                right = min(x + w, pil_img.width)
                bottom = min(y + h, pil_img.height)

                if right <= left or bottom <= top:
                    return error_response('invalid_request', 'Crop area outside image bounds.', status=400)

                # Re-square after clamping (a box grazing the image edge can lose its
                # 1:1 ratio), so the resize to the fixed tile never distorts.
                clamped_side = min(right - left, bottom - top)
                right = left + clamped_side
                bottom = top + clamped_side

                cropped = pil_img.crop((left, top, right, bottom))
                if cropped.mode not in ('RGB', 'L'):
                    cropped = cropped.convert('RGB')
                # Standardize to a fixed square training tile.
                cropped = cropped.resize((TRAINING_CROP_SIZE, TRAINING_CROP_SIZE), PILImage.LANCZOS)

                out = io.BytesIO()
                fmt = pil_img.format or 'JPEG'
                cropped.save(out, format=fmt)
                new_data = out.getvalue()
                
                new_sha256 = hashlib.sha256(new_data).hexdigest()
                new_size = len(new_data)

                existing_img = Image.query.filter_by(sha256=new_sha256).first()
                if existing_img and existing_img.id != img.id:
                    return error_response('conflict', 'This exact cropped image already exists in the database as another image.', status=409)

                # Overwrite the original object in place: same folder, same key.
                # Supabase uploads use x-upsert, so re-writing the key replaces
                # the blob; a source_path that is a real on-disk file is
                # rewritten directly (covers in-place ingested images too).
                if os.path.isabs(img.source_path) and os.path.exists(img.source_path):
                    Path(img.source_path).write_bytes(new_data)
                else:
                    storage.save_image(new_data, img.source_path, content_type=f'image/{fmt.lower()}')
    except (FileNotFoundError, OSError, storage.StorageError) as e:
        current_app.logger.error("Failed to process crop for %s: %s", img.id, e)
        return error_response('internal_error', f'Failed to process crop: {e}', status=500)

    img.sha256 = new_sha256
    img.width_px = cropped.width
    img.height_px = cropped.height
    img.file_size_bytes = new_size

    # Re-cropping invalidates existing annotations (coordinates no longer match).
    # Delete them via the ORM so the delete-orphan cascade removes their regions
    # and review_actions too -- a bulk query.delete() skips that cascade and trips
    # the regions FK on Postgres.
    for ann in ImageAnnotation.query.filter_by(image_id=img.id).all():
        db.session.delete(ann)
    DiscardedImage.query.filter_by(image_id=img.id).delete(synchronize_session=False)

    db.session.commit()

    return jsonify({'message': 'Image cropped and reset successfully', 'image': img.to_dict()})



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
        'image_phase': img.image_phase.value if img.image_phase else None,
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

    diagnosis_counts = dict(
        db.session.query(PatientDiagnosis.patient_code, func.count(PatientDiagnosis.id))
        .filter(PatientDiagnosis.status == AnnotationStatus.submitted)
        .group_by(PatientDiagnosis.patient_code)
        .all()
    )
    consensus_labels = dict(
        db.session.query(PatientConsensus.patient_code, PatientConsensus.label).all()
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
            'submitted_diagnoses': diagnosis_counts.get(code, 0),
            'consensus_label': consensus_labels.get(code),
        })

    return jsonify({'items': items, 'count': len(items), 'summary': summary})
