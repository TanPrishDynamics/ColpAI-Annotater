"""Consensus + agreement computation.

The functions here are pure-ish: they take a list of `ImageAnnotation` rows (or just
labels) and return either a `ConsensusLabel`-shaped dict or an agreement score.
They write to the DB only via the orchestration helpers at the bottom.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from typing import Iterable

from sqlalchemy import and_, select

from app.extensions import db
from app.models import ConsensusLabel, ImageAnnotation
from app.models.enums import AnnotationStatus, DiagnosisLabel


CONSIDERED_STATUSES = (
    AnnotationStatus.submitted,
    AnnotationStatus.reviewed,
    AnnotationStatus.consensus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def majority_label(annotations: list[ImageAnnotation]) -> tuple[DiagnosisLabel | None, float, list[str]]:
    """Pick the modal `colposcopic_impression`. Ties broken by mean confidence (higher wins).

    Returns (label, agreement_score, derived_from_ids). agreement_score is the share of
    annotators that picked the chosen label, in [0, 1]. None when the input is empty
    or no annotation has a label.
    """
    labels = [a.colposcopic_impression for a in annotations if a.colposcopic_impression is not None]
    if not labels:
        return None, 0.0, []

    counts = Counter(labels)
    top = counts.most_common()
    if not top:
        return None, 0.0, []

    top_count = top[0][1]
    tied = [lbl for lbl, n in top if n == top_count]
    if len(tied) == 1:
        winner = tied[0]
    else:
        # Tie-break by mean confidence among annotators that picked each tied label.
        def mean_conf(lbl: DiagnosisLabel) -> float:
            confs = [a.confidence for a in annotations
                     if a.colposcopic_impression == lbl and a.confidence is not None]
            return sum(confs) / len(confs) if confs else 0.0
        winner = max(tied, key=mean_conf)

    agreement = top_count / len(labels)
    derived = [a.id for a in annotations if a.colposcopic_impression == winner]
    return winner, agreement, derived


def percent_agreement(annotations: list[ImageAnnotation]) -> float | None:
    """Across all pairs of annotators, the fraction that agree on `colposcopic_impression`.

    Returns None if fewer than 2 annotators have labelled this image.
    """
    labels = [a.colposcopic_impression for a in annotations if a.colposcopic_impression is not None]
    if len(labels) < 2:
        return None
    pairs = list(combinations(labels, 2))
    agree = sum(1 for a, b in pairs if a == b)
    return agree / len(pairs)


def cohen_kappa(labels_a: list[str], labels_b: list[str]) -> float | None:
    """Cohen's kappa for two parallel label vectors. Returns None when undefined."""
    if len(labels_a) != len(labels_b) or not labels_a:
        return None
    n = len(labels_a)
    categories = set(labels_a) | set(labels_b)
    if not categories:
        return None
    p_o = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    count_a = Counter(labels_a)
    count_b = Counter(labels_b)
    p_e = sum((count_a[c] / n) * (count_b[c] / n) for c in categories)
    if p_e == 1.0:
        return 1.0 if p_o == 1.0 else None
    return (p_o - p_e) / (1 - p_e)


def pairwise_kappa(image_label_map: dict[str, dict[str, str]]) -> float | None:
    """Average pairwise Cohen's kappa across all (rater_i, rater_j) pairs.

    `image_label_map` maps annotator_id -> {image_id: label}. We compute kappa only on
    images both raters have labelled, then average across pairs.
    """
    raters = list(image_label_map.keys())
    if len(raters) < 2:
        return None
    kappas: list[float] = []
    for a, b in combinations(raters, 2):
        common_images = sorted(set(image_label_map[a]) & set(image_label_map[b]))
        if len(common_images) < 2:
            continue
        la = [image_label_map[a][img] for img in common_images]
        lb = [image_label_map[b][img] for img in common_images]
        k = cohen_kappa(la, lb)
        if k is not None:
            kappas.append(k)
    if not kappas:
        return None
    return sum(kappas) / len(kappas)


# ---------- DB orchestration ----------

def submitted_for_image(image_id: str) -> list[ImageAnnotation]:
    return db.session.execute(
        select(ImageAnnotation).where(and_(
            ImageAnnotation.image_id == image_id,
            ImageAnnotation.status.in_(CONSIDERED_STATUSES),
        ))
    ).scalars().all()


def upsert_consensus_for_image(image_id: str) -> ConsensusLabel | None:
    """Recompute and persist the consensus row for one image. Returns the row or None
    (when there's nothing to consensus over)."""
    annotations = submitted_for_image(image_id)
    label, agreement, derived = majority_label(annotations)
    if label is None or len(annotations) < 2:
        # Wipe stale consensus if requirements no longer met.
        existing = db.session.execute(
            select(ConsensusLabel).where(ConsensusLabel.image_id == image_id)
        ).scalar_one_or_none()
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()
        return None

    existing = db.session.execute(
        select(ConsensusLabel).where(ConsensusLabel.image_id == image_id)
    ).scalar_one_or_none()
    if existing is None:
        existing = ConsensusLabel(image_id=image_id)
        db.session.add(existing)
    existing.label = label
    existing.derived_from = derived
    existing.agreement_score = agreement
    existing.computed_at = _utcnow()
    db.session.commit()
    return existing


def recompute_all() -> dict[str, int]:
    """Walk every image that has 2+ submitted annotations and refresh consensus."""
    image_ids = [r[0] for r in db.session.execute(
        select(ImageAnnotation.image_id, db.func.count(ImageAnnotation.id))
        .where(ImageAnnotation.status.in_(CONSIDERED_STATUSES))
        .group_by(ImageAnnotation.image_id)
        .having(db.func.count(ImageAnnotation.id) >= 2)
    ).all()]
    updated = 0
    for img_id in image_ids:
        if upsert_consensus_for_image(img_id) is not None:
            updated += 1
    return {'eligible_images': len(image_ids), 'consensus_written': updated}


def find_disagreement_images() -> list[dict]:
    """Images with ≥2 submitted annotations whose `colposcopic_impression` doesn't match."""
    rows = db.session.execute(
        select(ImageAnnotation.image_id)
        .where(ImageAnnotation.status.in_(CONSIDERED_STATUSES))
        .group_by(ImageAnnotation.image_id)
        .having(db.func.count(ImageAnnotation.id) >= 2)
    ).all()
    out = []
    for (img_id,) in rows:
        anns = submitted_for_image(img_id)
        labels = {a.colposcopic_impression for a in anns if a.colposcopic_impression is not None}
        if len(labels) > 1:
            out.append({
                'image_id': img_id,
                'labels': sorted(l.value for l in labels),
                'annotator_count': len(anns),
                'agreement': percent_agreement(anns),
            })
    return out
