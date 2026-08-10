"""Review workflow API. Restricted to users with role `reviewer` or `admin`.

Endpoints:
- GET  /api/v1/review/queue            - submitted annotations awaiting review
- GET  /api/v1/review/diagnosis-queue  - submitted diagnoses awaiting review
- GET  /api/v1/review/disagreements    - patients where annotators disagree on diagnosis
- POST /api/v1/review/{annotation_id}/approve
- POST /api/v1/review/{annotation_id}/reject
- POST /api/v1/review/diagnosis/{patient_code}/approve
- POST /api/v1/review/diagnosis/{patient_code}/reject
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import and_, exists, select

from app.api.errors import error_response
from app.extensions import db
from app.models import (
    Image,
    ImageAnnotation,
    PatientDiagnosis,
    Region,
    ReviewAction,
    DiagnosisReviewAction,
)
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


# Everything the annotator filled in, carried forward when a rejection reopens an
# image. Excludes the lifecycle columns (status/version/timestamps) and crop_path,
# which only ever points at an image rendered on approval.
_ANNOTATION_CARRY_FORWARD = (
    'image_quality', 'blur_present', 'blood_present', 'mucus_present',
    'specular_reflection_present', 'lighting_issue', 'usable_for_training',
    'scj_visibility', 'transformation_zone_type', 'tz_visibility',
    'acetowhitening_severity', 'iodine_pattern', 'vascular_pattern',
    'color_tone', 'surface_contour', 'atypical_vessels_present',
    'ifcpc_grade', 'colposcopy_adequacy',
    'notes',
)

_REGION_CARRY_FORWARD = (
    'region_type', 'lesion_label', 'lesion_location_clock', 'lesion_quadrant',
    'lesion_size_percent', 'lesion_margins', 'punctation_present',
    'punctation_severity', 'mosaic_present', 'mosaic_severity', 'region_notes',
)


def _reopen_as_draft(ann: ImageAnnotation) -> ImageAnnotation:
    """Clone a rejected annotation into a fresh editable draft for its annotator.

    The rejected row stays `superseded` with its reject ReviewAction attached, so
    the audit trail (and the exporters' never-export-superseded rule) is intact.
    The annotator gets a new version pre-filled with everything they had -- form
    fields, crop box and every region -- so they correct their previous work
    instead of redoing the image from scratch.

    JSON columns are deep-copied: sharing the dict between the old and new row
    would let an edit on the draft silently rewrite the superseded record.
    """
    last_version = db.session.execute(
        select(db.func.max(ImageAnnotation.version)).where(and_(
            ImageAnnotation.image_id == ann.image_id,
            ImageAnnotation.annotator_id == ann.annotator_id,
        ))
    ).scalar() or 0

    redo = ImageAnnotation(
        image_id=ann.image_id,
        annotator_id=ann.annotator_id,
        status=AnnotationStatus.draft,
        version=last_version + 1,
        crop_box=deepcopy(ann.crop_box),
        **{field: getattr(ann, field) for field in _ANNOTATION_CARRY_FORWARD},
    )
    db.session.add(redo)

    for region in ann.regions:
        db.session.add(Region(
            annotation=redo,
            geometry=deepcopy(region.geometry),
            **{field: getattr(region, field) for field in _REGION_CARRY_FORWARD},
        ))

    return redo


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

    images = {img.id: img for img in db.session.execute(
        select(Image).where(Image.id.in_([a.image_id for a in items]))
    ).scalars().all()}

    def _item(a):
        img = images.get(a.image_id)
        return {
            **a.to_dict(include_regions=True),
            'patient_code': img.patient_code if img else None,
            'image_phase': img.image_phase.value if img and img.image_phase else None,
        }

    return jsonify({
        'items': [_item(a) for a in items],
        'next_cursor': next_cursor,
    })


@bp.get('/disagreements')
@login_required
def disagreements():
    guard = _require_reviewer()
    if guard is not None:
        return guard
    return jsonify({'items': consensus.find_disagreement_patients()})


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

    reopened_patient = None
    redo = None
    if action == ReviewActionType.approve:
        ann.status = AnnotationStatus.reviewed
        # Only once a reviewer approves do we render and store the final annotated
        # image (drawn regions composited on the crop) under annotated/<patient>/.
        # Non-fatal: a missing/unreadable source just leaves crop_path unset.
        if ann.crop_box or ann.regions:
            ann.crop_path = render_and_store_annotated(ann)
    elif action == ReviewActionType.reject:
        # Rejection reopens the whole patient case for this annotator. The flagged
        # image is superseded and immediately cloned into a fresh draft version
        # carrying all of its previous data, so the annotator edits and corrects
        # their work rather than starting over. Their patient diagnosis drops back
        # to draft -- so re-submitting the patient re-finalizes the corrected image
        # back into the review queue. The reject ReviewAction stays on the
        # superseded row for audit.
        ann.status = AnnotationStatus.superseded
        redo = _reopen_as_draft(ann)
        img = db.session.get(Image, ann.image_id)
        if img is not None and img.patient_code:
            pd = db.session.execute(
                select(PatientDiagnosis).where(and_(
                    PatientDiagnosis.patient_code == img.patient_code,
                    PatientDiagnosis.annotator_id == ann.annotator_id,
                    PatientDiagnosis.status == AnnotationStatus.submitted,
                ))
            ).scalar_one_or_none()
            if pd is not None:
                pd.status = AnnotationStatus.draft
                pd.submitted_at = None
                reopened_patient = img.patient_code
    db.session.commit()

    # A reopened patient diagnosis dropped out of the submitted set, so refresh
    # its consensus (it may fall below the 2-annotator threshold and be wiped).
    if reopened_patient:
        consensus.upsert_consensus_for_patient(reopened_patient)

    return jsonify({
        'annotation_id': ann.id,
        'new_status': ann.status.value,
        'action': action.value,
        'reopened_patient': reopened_patient,
        # The pre-filled draft the annotator now edits (reject only).
        'redo_annotation_id': redo.id if redo is not None else None,
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


@bp.get('/diagnosis-queue')
@login_required
def diagnosis_queue():
    """Submitted patient diagnoses that haven't been reviewed yet."""
    guard = _require_reviewer()
    if guard is not None:
        return guard

    query = ReviewQueueQuery.model_validate(request.args.to_dict())

    # Subquery for diagnoses that have NOT been reviewed
    stmt = (
        select(PatientDiagnosis)
        .where(PatientDiagnosis.status == AnnotationStatus.submitted)
        .where(
            ~exists(
                select(1).where(
                    DiagnosisReviewAction.patient_diagnosis_id == PatientDiagnosis.id
                )
            )
        )
    )
    if query.cursor:
        try:
            after = _decode_cursor(query.cursor)
        except Exception:
            return error_response('invalid_cursor', 'Cursor is malformed.', status=422)
        stmt = stmt.where(PatientDiagnosis.id > after)

    stmt = stmt.order_by(PatientDiagnosis.id.asc()).limit(query.limit + 1)
    rows = db.session.execute(stmt).scalars().all()
    has_more = len(rows) > query.limit
    items = rows[:query.limit]
    next_cursor = _encode_cursor(items[-1].id) if has_more and items else None

    return jsonify({
        'items': [item.to_dict() for item in items],
        'next_cursor': next_cursor,
    })


def _record_diagnosis_action(patient_code: str, action: ReviewActionType, comment: str | None):
    """Record approval/rejection of a patient diagnosis."""
    pd = db.session.execute(
        select(PatientDiagnosis).where(and_(
            PatientDiagnosis.patient_code == patient_code,
            PatientDiagnosis.status == AnnotationStatus.submitted,
        ))
    ).scalar_one_or_none()

    if pd is None:
        return error_response(
            'not_found',
            f'No submitted diagnosis found for patient {patient_code}.',
            status=404,
        )

    # Check if already reviewed
    existing = db.session.execute(
        select(DiagnosisReviewAction).where(
            DiagnosisReviewAction.patient_diagnosis_id == pd.id
        )
    ).scalar_one_or_none()

    if existing:
        return error_response(
            'already_reviewed',
            'This diagnosis has already been reviewed.',
            status=409,
        )

    db.session.add(DiagnosisReviewAction(
        patient_code=patient_code,
        patient_diagnosis_id=pd.id,
        reviewer_id=current_user.id,
        action=action,
        comment=comment,
    ))

    if action == ReviewActionType.approve:
        pd.status = AnnotationStatus.reviewed
    elif action == ReviewActionType.reject:
        # Reopen the diagnosis for the annotator to revise
        pd.status = AnnotationStatus.draft
        pd.submitted_at = None

    db.session.commit()

    return jsonify({
        'patient_code': patient_code,
        'diagnosis_id': pd.id,
        'new_status': pd.status.value,
        'action': action.value,
    })


@bp.post('/diagnosis/<patient_code>/approve')
@login_required
def approve_diagnosis(patient_code: str):
    guard = _require_reviewer()
    if guard is not None:
        return guard
    body = ReviewActionBody.model_validate(request.get_json(silent=True) or {})
    return _record_diagnosis_action(patient_code, ReviewActionType.approve, body.comment)


@bp.post('/diagnosis/<patient_code>/reject')
@login_required
def reject_diagnosis(patient_code: str):
    guard = _require_reviewer()
    if guard is not None:
        return guard
    body = ReviewActionBody.model_validate(request.get_json(silent=True) or {})
    return _record_diagnosis_action(patient_code, ReviewActionType.reject, body.comment)
