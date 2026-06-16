"""Image queue API. Read-only in Phase 1."""
import base64
import mimetypes
import os
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file, current_app
from flask_login import login_required, current_user
from sqlalchemy import and_, case, distinct, exists, func, select

from app.api.errors import error_response
from app.extensions import db
from app.models import Image, ImageAnnotation
from app.models.enums import AnnotationStatus, ImagePhase
from app.schemas.image import ImageQueueQuery, ImageOut, ImageQueueResponse
from app.services import storage

bp = Blueprint('images', __name__, url_prefix='/api/v1/images')

VALID_STATUSES = {'unannotated', 'mine', 'reviewed', 'all'}


def _encode_cursor(image_id: str) -> str:
    return base64.urlsafe_b64encode(image_id.encode()).decode().rstrip('=')


def _decode_cursor(cursor: str) -> str:
    padded = cursor + '=' * (-len(cursor) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode()


@bp.get('')
@login_required
def list_images():
    query = ImageQueueQuery.model_validate(request.args.to_dict())

    if query.status and query.status not in VALID_STATUSES:
        return error_response(
            'invalid_status',
            f"status must be one of {sorted(VALID_STATUSES)}",
            status=422,
        )

    stmt = select(Image)

    if query.phase:
        try:
            phase = ImagePhase(query.phase)
        except ValueError:
            return error_response('invalid_phase', f"Unknown image_phase: {query.phase}", status=422)
        stmt = stmt.where(Image.image_phase == phase)

    if query.dataset_source:
        stmt = stmt.where(Image.dataset_source == query.dataset_source)

    if query.patient_code:
        stmt = stmt.where(Image.patient_code == query.patient_code)

    # "Submitted+" = the user has effectively finished work on this image.
    SUBMITTED_LIKE = (
        AnnotationStatus.submitted,
        AnnotationStatus.reviewed,
        AnnotationStatus.consensus,
    )

    if query.status == 'mine':
        stmt = stmt.where(
            exists().where(and_(
                ImageAnnotation.image_id == Image.id,
                ImageAnnotation.annotator_id == current_user.id,
                ImageAnnotation.status.in_(SUBMITTED_LIKE),
            ))
        )
    elif query.status == 'unannotated':
        stmt = stmt.where(
            ~exists().where(and_(
                ImageAnnotation.image_id == Image.id,
                ImageAnnotation.annotator_id == current_user.id,
                ImageAnnotation.status.in_(SUBMITTED_LIKE),
            ))
        )
    elif query.status == 'reviewed':
        stmt = stmt.where(
            exists().where(and_(
                ImageAnnotation.image_id == Image.id,
                ImageAnnotation.status == AnnotationStatus.reviewed,
            ))
        )

    if query.cursor:
        try:
            after_id = _decode_cursor(query.cursor)
        except Exception:
            return error_response('invalid_cursor', 'Cursor is malformed.', status=422)
        stmt = stmt.where(Image.id > after_id)

    stmt = stmt.order_by(Image.id.asc()).limit(query.limit + 1)
    rows = db.session.execute(stmt).scalars().all()

    has_more = len(rows) > query.limit
    items = rows[:query.limit]
    next_cursor = _encode_cursor(items[-1].id) if has_more and items else None

    response = ImageQueueResponse(
        items=[ImageOut(**img.to_dict()) for img in items],
        next_cursor=next_cursor,
    )
    return jsonify(response.model_dump())


@bp.get('/<image_id>')
@login_required
def get_image(image_id: str):
    img = db.session.get(Image, image_id)
    if img is None:
        return error_response('not_found', 'Image not found.', status=404)
    return jsonify(ImageOut(**img.to_dict()).model_dump())


@bp.get('/<image_id>/file')
@login_required
def serve_image_file(image_id: str):
    img = db.session.get(Image, image_id)
    if img is None:
        return error_response('not_found', 'Image not found.', status=404)

    # Fast path: a real local file streams with range/conditional support.
    if os.path.isabs(img.source_path) and os.path.exists(img.source_path):
        return send_file(img.source_path, conditional=True)

    # Otherwise pull the bytes from the configured backend (e.g. Supabase).
    try:
        handle = storage.open_image(img.source_path)
    except (FileNotFoundError, storage.StorageError, OSError):
        return error_response(
            'file_missing',
            f"Source file is no longer available at {img.source_path}",
            status=410,
        )
    mimetype = mimetypes.guess_type(img.source_path)[0] or 'application/octet-stream'
    download_name = f"{img.id}{Path(img.source_path).suffix or ''}"
    return send_file(handle, mimetype=mimetype, download_name=download_name)


@bp.get('/datasets')
@login_required
def list_datasets():
    """Distinct dataset_source values currently in the DB."""
    rows = db.session.execute(
        select(Image.dataset_source).distinct().order_by(Image.dataset_source)
    ).scalars().all()
    return jsonify({'datasets': rows})


@bp.get('/patients')
@login_required
def list_patients():
    """Distinct patient codes with how many images the current user still has to do.

    ``remaining`` = images in the patient that the user hasn't submitted (or better)
    yet, so the annotate page can let a doctor pick a patient and see what's left.
    """
    submitted_like = (
        AnnotationStatus.submitted,
        AnnotationStatus.reviewed,
        AnnotationStatus.consensus,
    )
    mine_done = case(
        (and_(
            ImageAnnotation.annotator_id == current_user.id,
            ImageAnnotation.status.in_(submitted_like),
        ), Image.id),
        else_=None,
    )
    rows = (
        db.session.query(
            Image.patient_code,
            func.count(distinct(Image.id)).label('total'),
            func.count(distinct(mine_done)).label('done'),
        )
        .outerjoin(ImageAnnotation, ImageAnnotation.image_id == Image.id)
        .filter(Image.patient_code.isnot(None))
        .group_by(Image.patient_code)
        .order_by(Image.patient_code)
        .all()
    )
    items = [{
        'patient_code': code,
        'total': total,
        'done': done,
        'remaining': total - done,
    } for code, total, done in rows]
    return jsonify({'items': items})


@bp.get('/stats/queue')
@login_required
def queue_stats():
    """Counts: total images, submitted by me, remaining. Drafts don't count as annotated."""
    total = db.session.execute(select(db.func.count(Image.id))).scalar_one()
    mine = db.session.execute(
        select(db.func.count(db.func.distinct(ImageAnnotation.image_id)))
        .where(and_(
            ImageAnnotation.annotator_id == current_user.id,
            ImageAnnotation.status.in_((
                AnnotationStatus.submitted,
                AnnotationStatus.reviewed,
                AnnotationStatus.consensus,
            )),
        ))
    ).scalar_one()
    return jsonify({
        'total_images': total,
        'annotated_by_me': mine,
        'remaining_for_me': max(total - mine, 0),
    })
