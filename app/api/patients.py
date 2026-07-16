"""Patient-level FINAL diagnosis API.

Diagnosis lifecycle (no review flow -- consensus across annotators is the quality
mechanism, not reviewer approval):
- A user has at most one `PatientDiagnosis` row per patient (draft or submitted).
  POST /diagnosis either returns the existing row or creates a draft.
- PATCH autosave only mutates drafts. Submitted rows are immutable via this API.
- Submit flips the draft to `submitted`, recomputes patient consensus, AND finalizes
  (draft -> submitted) every one of this annotator's ImageAnnotation drafts for the
  patient's images -- there's no per-image submit; annotators just autosave their
  way through the images and everything is finalized together at the end.

Images without a `patient_code` have no patient diagnosis -- every endpoint here
404s for a code with no images.
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import and_, exists, func, select

from app.api.errors import error_response
from app.extensions import db
from app.models import Image, ImageAnnotation, PatientConsensus, PatientDiagnosis, ReviewAction
from app.models.enums import AnnotationStatus, ReviewActionType
from app.schemas.image import ImageOut
from app.schemas.patient import PatientDiagnosisPatch, PatientDiagnosisSubmit
from app.services import consensus

bp = Blueprint('patients', __name__, url_prefix='/api/v1/patients')


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _patient_exists(patient_code: str) -> bool:
    return db.session.execute(
        select(exists().where(Image.patient_code == patient_code))
    ).scalar()


def _apply_fields(row: PatientDiagnosis, payload: PatientDiagnosisPatch) -> None:
    if payload.colposcopic_impression is not None:
        row.colposcopic_impression = [v.value for v in payload.colposcopic_impression]
    if payload.histopathology_result is not None:
        row.histopathology_result = payload.histopathology_result
    if payload.confidence is not None:
        row.confidence = payload.confidence
    if payload.notes is not None:
        row.notes = payload.notes
    for field in ('cytology_result', 'hpv_status', 'management_recommendation', 'biopsy_taken',
                  'reid_margin', 'reid_color', 'reid_vessels', 'reid_iodine',
                  'swede_aceto', 'swede_margin', 'swede_vessels', 'swede_size', 'swede_iodine'):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)


def _load_own_diagnosis(patient_code: str) -> PatientDiagnosis | None:
    return db.session.execute(
        select(PatientDiagnosis).where(and_(
            PatientDiagnosis.patient_code == patient_code,
            PatientDiagnosis.annotator_id == current_user.id,
        ))
    ).scalar_one_or_none()


def _consensus_dict(patient_code: str) -> dict | None:
    row = db.session.execute(
        select(PatientConsensus).where(PatientConsensus.patient_code == patient_code)
    ).scalar_one_or_none()
    return row.to_dict() if row is not None else None


def _finalize_image_annotations(patient_code: str) -> int:
    """Submit every draft ImageAnnotation the current user has for this patient's
    images. There's no per-image submit anymore -- the annotator just autosaves
    drafts while working through the images, and everything is finalized together
    here, when the patient's diagnosis is submitted. Images the annotator never
    opened (no draft) are left alone. Returns how many were finalized."""
    drafts = db.session.execute(
        select(ImageAnnotation).where(and_(
            ImageAnnotation.annotator_id == current_user.id,
            ImageAnnotation.status == AnnotationStatus.draft,
            ImageAnnotation.image_id.in_(
                select(Image.id).where(Image.patient_code == patient_code)
            ),
        ))
    ).scalars().all()
    now = _utcnow()
    for ann in drafts:
        ann.status = AnnotationStatus.submitted
        ann.submitted_at = now
    return len(drafts)


@bp.get('')
@login_required
def list_patients():
    """Per-patient diagnosis progress for the current user, for the annotate-page
    patient picker: total images plus this user's diagnosis status."""
    rows = (
        db.session.query(
            Image.patient_code,
            func.count(func.distinct(Image.id)).label('total'),
        )
        .filter(Image.patient_code.isnot(None))
        .group_by(Image.patient_code)
        .order_by(Image.patient_code)
        .all()
    )

    my_diagnoses = {
        d.patient_code: d.status.value
        for d in db.session.execute(
            select(PatientDiagnosis).where(PatientDiagnosis.annotator_id == current_user.id)
        ).scalars().all()
    }
    submitted_counts = dict(
        db.session.query(PatientDiagnosis.patient_code, func.count(PatientDiagnosis.id))
        .filter(PatientDiagnosis.status == AnnotationStatus.submitted)
        .group_by(PatientDiagnosis.patient_code)
        .all()
    )

    items = [{
        'patient_code': code,
        'total_images': total,
        'my_diagnosis_status': my_diagnoses.get(code),
        'submitted_diagnoses': submitted_counts.get(code, 0),
    } for code, total in rows]
    return jsonify({'items': items})


@bp.get('/<patient_code>')
@login_required
def get_patient(patient_code: str):
    if not _patient_exists(patient_code):
        return error_response('patient_not_found', f'No images for patient {patient_code}.', status=404)

    images = db.session.execute(
        select(Image).where(Image.patient_code == patient_code).order_by(Image.id.asc())
    ).scalars().all()

    image_ids = [img.id for img in images]
    my_annotations = {
        a.image_id: a.status.value
        for a in db.session.execute(
            select(ImageAnnotation).where(and_(
                ImageAnnotation.image_id.in_(image_ids),
                ImageAnnotation.annotator_id == current_user.id,
                ImageAnnotation.status != AnnotationStatus.superseded,
            ))
        ).scalars().all()
    }

    # Reviewer rejections still awaiting a fix: the latest reject comment on this
    # user's annotation for each image where they don't yet have a live (non-
    # superseded) annotation -- i.e. the image was bounced back and not redone.
    my_rejections: dict[str, str | None] = {}
    reject_rows = db.session.execute(
        select(ImageAnnotation.image_id, ReviewAction.comment, ReviewAction.created_at)
        .join(ReviewAction, ReviewAction.image_annotation_id == ImageAnnotation.id)
        .where(and_(
            ImageAnnotation.image_id.in_(image_ids),
            ImageAnnotation.annotator_id == current_user.id,
            ReviewAction.action == ReviewActionType.reject,
        ))
        .order_by(ReviewAction.created_at.desc())
    ).all()
    for image_id, comment, _created in reject_rows:
        # Only flag images still needing work, keeping the most recent comment.
        if image_id not in my_annotations and image_id not in my_rejections:
            my_rejections[image_id] = comment

    image_items = []
    for img in images:
        item = ImageOut(**img.to_dict()).model_dump()
        item['my_annotation_status'] = my_annotations.get(img.id)
        item['needs_redo'] = img.id in my_rejections           # reviewer flagged, not yet redone
        item['reviewer_rejection'] = my_rejections.get(img.id)  # the reviewer's comment (may be null)
        image_items.append(item)

    my_diagnosis = _load_own_diagnosis(patient_code)
    submitted_count = db.session.execute(
        select(func.count(PatientDiagnosis.id)).where(and_(
            PatientDiagnosis.patient_code == patient_code,
            PatientDiagnosis.status == AnnotationStatus.submitted,
        ))
    ).scalar_one()

    return jsonify({
        'patient_code': patient_code,
        'images': image_items,
        'my_diagnosis': my_diagnosis.to_dict() if my_diagnosis else None,
        'submitted_diagnosis_count': submitted_count,
        'consensus': _consensus_dict(patient_code),
    })


@bp.post('/<patient_code>/diagnosis')
@login_required
def create_or_get_diagnosis(patient_code: str):
    """Idempotent: returns the user's existing diagnosis row for this patient, or creates a draft."""
    if not _patient_exists(patient_code):
        return error_response('patient_not_found', f'No images for patient {patient_code}.', status=404)

    existing = _load_own_diagnosis(patient_code)
    if existing is not None:
        return jsonify(existing.to_dict())

    row = PatientDiagnosis(
        patient_code=patient_code,
        annotator_id=current_user.id,
        status=AnnotationStatus.draft,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify(row.to_dict()), 201


@bp.patch('/<patient_code>/diagnosis')
@login_required
def autosave_diagnosis(patient_code: str):
    row = _load_own_diagnosis(patient_code)
    if row is None:
        return error_response('not_found', 'No diagnosis draft for this patient.', status=404)
    if row.status != AnnotationStatus.draft:
        return error_response(
            'not_editable',
            f'Diagnosis is {row.status.value}; only drafts can be autosaved.',
            status=409,
        )

    payload = PatientDiagnosisPatch.model_validate(request.get_json(silent=True) or {})
    _apply_fields(row, payload)
    db.session.commit()
    return jsonify({
        'id': row.id,
        'status': row.status.value,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    })


@bp.post('/<patient_code>/diagnosis/submit')
@login_required
def submit_diagnosis(patient_code: str):
    row = _load_own_diagnosis(patient_code)
    if row is None:
        return error_response('not_found', 'No diagnosis draft for this patient.', status=404)
    if row.status != AnnotationStatus.draft:
        return error_response(
            'already_submitted',
            f'Diagnosis is already {row.status.value}.',
            status=409,
        )

    body = request.get_json(silent=True) or {}
    if body:
        patch = PatientDiagnosisPatch.model_validate(body)
        _apply_fields(row, patch)

    PatientDiagnosisSubmit.model_validate({
        'colposcopic_impression': row.colposcopic_impression or [],
        'confidence': row.confidence,
    })

    row.status = AnnotationStatus.submitted
    row.submitted_at = _utcnow()
    images_finalized = _finalize_image_annotations(patient_code)
    db.session.commit()

    consensus.upsert_consensus_for_patient(patient_code)

    return jsonify({
        'diagnosis': row.to_dict(),
        'consensus': _consensus_dict(patient_code),
        'images_finalized': images_finalized,
    })
