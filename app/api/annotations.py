"""Annotation API: draft / autosave / submit / discard.

Draft semantics:
- A user has at most one *non-superseded* draft per image. POST /annotations either
  returns the existing draft or creates a new one.
- PATCH autosave only mutates drafts. Submitted/superseded rows are immutable.
- Submit flips the draft to `submitted` and bumps version on next draft.
- Discard records a DiscardedImage row and superseded the user's draft (if any) for that image.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import and_, select

from app.api.errors import error_response
from app.extensions import db
from app.models import DiscardedImage, Image, ImageAnnotation
from app.models.enums import AnnotationStatus
from app.schemas.annotation import (
    AnnotationCreate,
    AnnotationListQuery,
    AnnotationPatch,
    AnnotationSubmit,
    DiscardRequest,
)

bp = Blueprint('annotations', __name__, url_prefix='/api/v1/annotations')


def _encode_cursor(annotation_id: str) -> str:
    return base64.urlsafe_b64encode(annotation_id.encode()).decode().rstrip('=')


def _decode_cursor(cursor: str) -> str:
    padded = cursor + '=' * (-len(cursor) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _apply_blocks(ann: ImageAnnotation, payload) -> None:
    """Copy any provided block fields onto the annotation row. Unset fields are left alone."""
    if payload.quality is not None:
        q = payload.quality
        for field in ('image_quality', 'blur_present', 'blood_present', 'mucus_present',
                      'specular_reflection_present', 'lighting_issue', 'usable_for_training'):
            value = getattr(q, field)
            if value is not None:
                setattr(ann, field, value)
    if payload.anatomy is not None:
        a = payload.anatomy
        for field in ('scj_visibility', 'transformation_zone_type', 'tz_visibility'):
            value = getattr(a, field)
            if value is not None:
                setattr(ann, field, value)
    if payload.features is not None:
        f = payload.features
        for field in ('acetowhitening_severity', 'iodine_pattern', 'vascular_pattern',
                      'color_tone', 'surface_contour', 'atypical_vessels_present'):
            value = getattr(f, field)
            if value is not None:
                setattr(ann, field, value)
    if payload.diagnosis is not None:
        d = payload.diagnosis
        for field in ('colposcopic_impression', 'histopathology_result', 'confidence', 'notes'):
            value = getattr(d, field)
            if value is not None:
                setattr(ann, field, value)


def _load_owned(annotation_id: str) -> ImageAnnotation | None:
    """Fetch an annotation that belongs to current_user. Returns None if missing or not owned."""
    ann = db.session.get(ImageAnnotation, annotation_id)
    if ann is None or ann.annotator_id != current_user.id:
        return None
    return ann


@bp.get('')
@login_required
def list_annotations():
    query = AnnotationListQuery.model_validate(request.args.to_dict())

    stmt = select(ImageAnnotation)
    if query.image_id:
        stmt = stmt.where(ImageAnnotation.image_id == query.image_id)
    if query.annotator_id:
        stmt = stmt.where(ImageAnnotation.annotator_id == query.annotator_id)
    if query.status:
        stmt = stmt.where(ImageAnnotation.status == query.status)

    if query.cursor:
        try:
            after_id = _decode_cursor(query.cursor)
        except Exception:
            return error_response('invalid_cursor', 'Cursor is malformed.', status=422)
        stmt = stmt.where(ImageAnnotation.id > after_id)

    stmt = stmt.order_by(ImageAnnotation.id.asc()).limit(query.limit + 1)
    rows = db.session.execute(stmt).scalars().all()

    has_more = len(rows) > query.limit
    items = rows[:query.limit]
    next_cursor = _encode_cursor(items[-1].id) if has_more and items else None

    return jsonify({
        'items': [a.to_dict(include_regions=False) for a in items],
        'next_cursor': next_cursor,
    })


@bp.post('')
@login_required
def create_or_get_draft():
    """Idempotent: returns the user's existing live annotation for this image, or creates a draft.

    "Live" = not superseded. If the user already submitted but hasn't been reviewed yet,
    we return that submitted row read-only rather than spawning a parallel draft.
    """
    payload = AnnotationCreate.model_validate(request.get_json(silent=True) or {})

    img = db.session.get(Image, payload.image_id)
    if img is None:
        return error_response('image_not_found', f'Image {payload.image_id} not found.', status=404)

    existing = db.session.execute(
        select(ImageAnnotation)
        .where(and_(
            ImageAnnotation.image_id == payload.image_id,
            ImageAnnotation.annotator_id == current_user.id,
            ImageAnnotation.status != AnnotationStatus.superseded,
        ))
        .order_by(ImageAnnotation.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return jsonify(existing.to_dict(include_regions=True))

    last_superseded_version = db.session.execute(
        select(db.func.max(ImageAnnotation.version))
        .where(and_(
            ImageAnnotation.image_id == payload.image_id,
            ImageAnnotation.annotator_id == current_user.id,
        ))
    ).scalar()
    next_version = (last_superseded_version or 0) + 1

    ann = ImageAnnotation(
        image_id=payload.image_id,
        annotator_id=current_user.id,
        status=AnnotationStatus.draft,
        version=next_version,
    )
    db.session.add(ann)
    db.session.commit()
    return jsonify(ann.to_dict(include_regions=True)), 201


@bp.get('/mine')
@login_required
def get_my_live_annotation():
    """Return the current user's non-superseded annotation for `image_id`, if any.

    Returns 204 when the user has not opened a draft yet, so the frontend can decide
    whether to create one lazily on the first edit.
    """
    image_id = request.args.get('image_id')
    if not image_id:
        return error_response('missing_param', 'image_id is required.', status=422)
    existing = db.session.execute(
        select(ImageAnnotation)
        .where(and_(
            ImageAnnotation.image_id == image_id,
            ImageAnnotation.annotator_id == current_user.id,
            ImageAnnotation.status != AnnotationStatus.superseded,
        ))
        .order_by(ImageAnnotation.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is None:
        return '', 204
    return jsonify(existing.to_dict(include_regions=True))


@bp.get('/<annotation_id>')
@login_required
def get_annotation(annotation_id: str):
    ann = db.session.get(ImageAnnotation, annotation_id)
    if ann is None:
        return error_response('not_found', 'Annotation not found.', status=404)
    if ann.annotator_id != current_user.id and current_user.role.value not in {'reviewer', 'admin'}:
        return error_response('forbidden', 'You cannot read another annotator\'s work.', status=403)
    return jsonify(ann.to_dict(include_regions=True))


@bp.patch('/<annotation_id>')
@login_required
def autosave(annotation_id: str):
    ann = _load_owned(annotation_id)
    if ann is None:
        return error_response('not_found', 'Annotation not found.', status=404)
    if ann.status != AnnotationStatus.draft:
        return error_response(
            'not_editable',
            f'Annotation is {ann.status.value}; only drafts can be autosaved.',
            status=409,
        )

    payload = AnnotationPatch.model_validate(request.get_json(silent=True) or {})
    _apply_blocks(ann, payload)
    db.session.commit()
    return jsonify({
        'id': ann.id,
        'status': ann.status.value,
        'updated_at': ann.updated_at.isoformat() if ann.updated_at else None,
    })


@bp.post('/<annotation_id>/submit')
@login_required
def submit(annotation_id: str):
    ann = _load_owned(annotation_id)
    if ann is None:
        return error_response('not_found', 'Annotation not found.', status=404)
    if ann.status != AnnotationStatus.draft:
        return error_response(
            'already_submitted',
            f'Annotation is already {ann.status.value}.',
            status=409,
        )

    # Final autosave-like merge before validating.
    body = request.get_json(silent=True) or {}
    if body:
        patch = AnnotationPatch.model_validate(body)
        _apply_blocks(ann, patch)

    # Re-validate the merged row against the submit schema.
    AnnotationSubmit.model_validate({
        'diagnosis': {
            'colposcopic_impression': ann.colposcopic_impression.value if ann.colposcopic_impression else None,
            'confidence': ann.confidence,
        },
    })

    ann.status = AnnotationStatus.submitted
    ann.submitted_at = _utcnow()
    db.session.commit()
    return jsonify(ann.to_dict(include_regions=True))


@bp.post('/<annotation_id>/discard')
@login_required
def discard(annotation_id: str):
    ann = _load_owned(annotation_id)
    if ann is None:
        return error_response('not_found', 'Annotation not found.', status=404)

    payload = DiscardRequest.model_validate(request.get_json(silent=True) or {})

    db.session.add(DiscardedImage(
        image_id=ann.image_id,
        annotator_id=current_user.id,
        reason=payload.reason,
    ))
    ann.status = AnnotationStatus.superseded
    db.session.commit()
    return jsonify({'status': 'discarded', 'annotation_id': ann.id})
