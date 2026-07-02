"""Dashboard analytics API.

Returns aggregates the frontend can plot directly. All endpoints are login_required;
admin role unlocks site-wide views, others see their own stats only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import and_, func, select

from app.api.errors import error_response
from app.extensions import db
from app.models import Image, ImageAnnotation, ReviewAction, User
from app.models.enums import AnnotationStatus, ReviewActionType, UserRole
from app.services import consensus

bp = Blueprint('dashboard', __name__, url_prefix='/api/v1/dashboard')


SUBMITTED_LIKE = (
    AnnotationStatus.submitted,
    AnnotationStatus.reviewed,
    AnnotationStatus.consensus,
)


def _is_admin() -> bool:
    return current_user.role.value in {UserRole.admin.value, UserRole.reviewer.value}


@bp.get('/stats')
@login_required
def stats():
    """Site-wide counts (admins/reviewers) or self counts (annotators)."""
    total_images = db.session.execute(select(func.count(Image.id))).scalar_one()
    total_users = db.session.execute(select(func.count(User.id))).scalar_one()
    total_annotators = db.session.execute(
        select(func.count(func.distinct(ImageAnnotation.annotator_id)))
        .where(ImageAnnotation.status.in_(SUBMITTED_LIKE))
    ).scalar_one()

    if _is_admin():
        submitted = db.session.execute(
            select(func.count(ImageAnnotation.id))
            .where(ImageAnnotation.status == AnnotationStatus.submitted)
        ).scalar_one()
        reviewed = db.session.execute(
            select(func.count(ImageAnnotation.id))
            .where(ImageAnnotation.status == AnnotationStatus.reviewed)
        ).scalar_one()
        drafts = db.session.execute(
            select(func.count(ImageAnnotation.id))
            .where(ImageAnnotation.status == AnnotationStatus.draft)
        ).scalar_one()
        unique_images = db.session.execute(
            select(func.count(func.distinct(ImageAnnotation.image_id)))
            .where(ImageAnnotation.status.in_(SUBMITTED_LIKE))
        ).scalar_one()
        avg_raters = unique_images and (
            db.session.execute(
                select(func.count(ImageAnnotation.id))
                .where(ImageAnnotation.status.in_(SUBMITTED_LIKE))
            ).scalar_one() / unique_images
        ) or 0.0
    else:
        submitted = db.session.execute(
            select(func.count(ImageAnnotation.id))
            .where(and_(
                ImageAnnotation.annotator_id == current_user.id,
                ImageAnnotation.status == AnnotationStatus.submitted,
            ))
        ).scalar_one()
        reviewed = db.session.execute(
            select(func.count(ImageAnnotation.id))
            .where(and_(
                ImageAnnotation.annotator_id == current_user.id,
                ImageAnnotation.status == AnnotationStatus.reviewed,
            ))
        ).scalar_one()
        drafts = db.session.execute(
            select(func.count(ImageAnnotation.id))
            .where(and_(
                ImageAnnotation.annotator_id == current_user.id,
                ImageAnnotation.status == AnnotationStatus.draft,
            ))
        ).scalar_one()
        unique_images = db.session.execute(
            select(func.count(func.distinct(ImageAnnotation.image_id)))
            .where(and_(
                ImageAnnotation.annotator_id == current_user.id,
                ImageAnnotation.status.in_(SUBMITTED_LIKE),
            ))
        ).scalar_one()
        avg_raters = None

    return jsonify({
        'scope': 'site' if _is_admin() else 'self',
        'total_images': total_images,
        'total_users': total_users,
        'active_annotators': total_annotators,
        'submitted_annotations': submitted,
        'reviewed_annotations': reviewed,
        'drafts_in_progress': drafts,
        'images_with_annotations': unique_images,
        'average_raters_per_image': round(avg_raters, 2) if avg_raters else None,
    })


@bp.get('/distribution')
@login_required
def distribution():
    """Counts of `colposcopic_impression` across submitted annotations."""
    stmt = (
        select(ImageAnnotation.colposcopic_impression)
        .where(ImageAnnotation.status.in_(SUBMITTED_LIKE))
        .where(ImageAnnotation.colposcopic_impression.isnot(None))
    )
    if not _is_admin():
        stmt = stmt.where(ImageAnnotation.annotator_id == current_user.id)
        
    rows = db.session.execute(stmt).scalars().all()
    counts = defaultdict(int)
    for labels in rows:
        if isinstance(labels, list):
            for label in labels:
                counts[label] += 1
                
    return jsonify({
        'items': [{'label': label, 'count': n} for label, n in counts.items()]
    })


@bp.get('/productivity')
@login_required
def productivity():
    """Submissions per day per annotator over the last `days` days (default 14)."""
    days = int(request.args.get('days', 14))
    days = max(1, min(days, 90))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            User.username,
            func.date(ImageAnnotation.submitted_at).label('day'),
            func.count(ImageAnnotation.id),
        )
        .join(User, User.id == ImageAnnotation.annotator_id)
        .where(ImageAnnotation.status.in_(SUBMITTED_LIKE))
        .where(ImageAnnotation.submitted_at.isnot(None))
        .where(ImageAnnotation.submitted_at >= cutoff)
    )
    if not _is_admin():
        stmt = stmt.where(ImageAnnotation.annotator_id == current_user.id)
    stmt = stmt.group_by(User.username, 'day').order_by(User.username, 'day')

    rows = db.session.execute(stmt).all()
    by_user: dict[str, list[dict]] = defaultdict(list)
    for username, day, n in rows:
        by_user[username].append({'date': str(day), 'count': n})
    return jsonify({
        'days': days,
        'series': [{'annotator': u, 'points': pts} for u, pts in by_user.items()],
    })


@bp.get('/agreement')
@login_required
def agreement():
    """Pairwise Cohen's kappa on colposcopic_impression, averaged across rater pairs."""
    # Build annotator_id -> {image_id: label} map from submitted annotations.
    stmt = (
        select(
            ImageAnnotation.annotator_id,
            ImageAnnotation.image_id,
            ImageAnnotation.colposcopic_impression,
        )
        .where(ImageAnnotation.status.in_(SUBMITTED_LIKE))
        .where(ImageAnnotation.colposcopic_impression.isnot(None))
    )
    rows = db.session.execute(stmt).all()

    by_rater: dict[str, dict[str, tuple]] = defaultdict(dict)
    for annotator_id, image_id, labels in rows:
        if isinstance(labels, list) and labels:
            by_rater[annotator_id][image_id] = tuple(sorted(labels))

    kappa = consensus.pairwise_kappa(by_rater)

    # Also compute mean percent-agreement for an easier-to-read second number.
    image_to_labels: dict[str, list] = defaultdict(list)
    for annotator_id, image_id, labels in rows:
        if isinstance(labels, list) and labels:
            image_to_labels[image_id].append(tuple(sorted(labels)))
    agreements: list[float] = []
    for image_id, label_tuples in image_to_labels.items():
        if len(label_tuples) < 2:
            continue
        # percent agreement = pairs that match / total pairs.
        from itertools import combinations
        pairs = list(combinations(label_tuples, 2))
        agreements.append(sum(1 for a, b in pairs if a == b) / len(pairs))

    return jsonify({
        'mean_kappa': round(kappa, 4) if kappa is not None else None,
        'mean_percent_agreement': round(sum(agreements)/len(agreements), 4) if agreements else None,
        'rater_count': len(by_rater),
        'multi_rater_images': len(agreements),
        'interpretation': _kappa_band(kappa),
    })


def _kappa_band(k: float | None) -> str | None:
    if k is None:
        return None
    if k < 0:    return 'worse than chance'
    if k < 0.20: return 'slight'
    if k < 0.40: return 'fair'
    if k < 0.60: return 'moderate'
    if k < 0.80: return 'substantial'
    return 'almost perfect'


@bp.get('/recent')
@login_required
def recent():
    """Latest submitted annotations (admin: all; otherwise own only)."""
    limit = min(int(request.args.get('limit', 10)), 50)
    stmt = (
        select(ImageAnnotation)
        .where(ImageAnnotation.status.in_(SUBMITTED_LIKE))
        .order_by(ImageAnnotation.submitted_at.desc().nullslast())
        .limit(limit)
    )
    if not _is_admin():
        stmt = stmt.where(ImageAnnotation.annotator_id == current_user.id)
    rows = db.session.execute(stmt).scalars().all()
    return jsonify({
        'items': [{
            **a.to_dict(include_regions=False),
            'annotator_username': a.annotator.username if a.annotator else None,
        } for a in rows],
    })
