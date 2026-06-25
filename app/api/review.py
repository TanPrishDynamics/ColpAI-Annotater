"""Review workflow API. Restricted to users with role `reviewer` or `admin`.

Endpoints:
- GET  /api/v1/review/queue            - submitted annotations awaiting review
- GET  /api/v1/review/disagreements    - images where annotators disagree on impression
- POST /api/v1/review/{annotation_id}/approve
- POST /api/v1/review/{annotation_id}/reject
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import and_, exists, select

from app.api.errors import error_response
from app.extensions import db
from app.models import ImageAnnotation, ReviewAction
from app.models.enums import AnnotationStatus, ReviewActionType, UserRole
from app.schemas.review import ReviewActionBody, ReviewQueueQuery
from app.services import consensus
from app.services.crop import render_and_store_annotated

bp = Blueprint('review', __name__, url_prefix='/api/v1/review')


REVIEWER_ROLES = {UserRole.reviewer.value, UserRole.admin.value}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _encode_cursor(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip('=')


def _decode_cursor(s: str) -> str:
    padded = s + '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode()


def _require_reviewer():
    if current_user.role.value not in REVIEWER_ROLES:
        return error_response('forbidden', 'Reviewer or admin role required.', status=403)
    return None


@bp.get('/queue')
@login_required
def queue():
    """Submitted annotations that haven't been approved/rejected yet."""
    guard = _require_reviewer()
    if guard is not None:
        return guard

    query = ReviewQueueQuery.model_validate(request.args.to_dict())

    stmt = (
        select(ImageAnnotation)
        .where(ImageAnnotation.status == AnnotationStatus.submitted)
        .where(~exists().where(ReviewAction.image_annotation_id == ImageAnnotation.id))
    )
    if query.annotator_id:
        stmt = stmt.where(ImageAnnotation.annotator_id == query.annotator_id)
    if query.image_id:
        stmt = stmt.where(ImageAnnotation.image_id == query.image_id)
    if query.cursor:
        try:
            after = _decode_cursor(query.cursor)
        except Exception:
            return error_response('invalid_cursor', 'Cursor is malformed.', status=422)
        stmt = stmt.where(ImageAnnotation.id > after)

    stmt = stmt.order_by(ImageAnnotation.id.asc()).limit(query.limit + 1)
    rows = db.session.execute(stmt).scalars().all()
    has_more = len(rows) > query.limit
    items = rows[:query.limit]
    next_cursor = _encode_cursor(items[-1].id) if has_more and items else None

    return jsonify({
        'items': [a.to_dict(include_regions=True) for a in items],
        'next_cursor': next_cursor,
    })


@bp.get('/disagreements')
@login_required
def disagreements():
    guard = _require_reviewer()
    if guard is not None:
        return guard
    return jsonify({'items': consensus.find_disagreement_images()})


def _record_action(annotation_id: str, action: ReviewActionType, comment: str | None):
    ann = db.session.get(ImageAnnotation, annotation_id)
    if ann is None:
        return error_response('not_found', 'Annotation not found.', status=404)
    if ann.status != AnnotationStatus.submitted:
        return error_response(
            'not_reviewable',
            f'Annotation is {ann.status.value}; only submitted annotations can be reviewed.',
            status=409,
        )

    db.session.add(ReviewAction(
        image_annotation_id=ann.id,
        reviewer_id=current_user.id,
        action=action,
        comment=comment,
    ))

    if action == ReviewActionType.approve:
        ann.status = AnnotationStatus.reviewed
        # Only once a reviewer approves do we render and store the final annotated
        # image (drawn regions composited on the crop) under annotated/<patient>/.
        # Non-fatal: a missing/unreadable source just leaves crop_path unset.
        if ann.crop_box or ann.regions:
            ann.crop_path = render_and_store_annotated(ann)
    elif action == ReviewActionType.reject:
        # Rejection sends the annotator back to drafting (new version).
        ann.status = AnnotationStatus.superseded
    db.session.commit()

    # Refresh consensus opportunistically.
    consensus.upsert_consensus_for_image(ann.image_id)

    return jsonify({
        'annotation_id': ann.id,
        'new_status': ann.status.value,
        'action': action.value,
    })


@bp.post('/<annotation_id>/approve')
@login_required
def approve(annotation_id: str):
    guard = _require_reviewer()
    if guard is not None:
        return guard
    body = ReviewActionBody.model_validate(request.get_json(silent=True) or {})
    return _record_action(annotation_id, ReviewActionType.approve, body.comment)


@bp.post('/<annotation_id>/reject')
@login_required
def reject(annotation_id: str):
    guard = _require_reviewer()
    if guard is not None:
        return guard
    body = ReviewActionBody.model_validate(request.get_json(silent=True) or {})
    return _record_action(annotation_id, ReviewActionType.reject, body.comment)
