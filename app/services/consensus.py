"""Consensus + agreement computation, at the patient level.

The pure functions here (`majority_label`, `percent_agreement`, `cohen_kappa`,
`pairwise_kappa`) take any rows that expose `.colposcopic_impression`, `.confidence`
and `.id` -- today that's `PatientDiagnosis`. They write to the DB only via the
orchestration helpers at the bottom.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from typing import Sequence

from sqlalchemy import and_, select

from app.extensions import db
from app.models import PatientConsensus, PatientDiagnosis
from app.models.enums import AnnotationStatus


# Patient diagnoses have no review flow, so only `submitted` counts toward consensus.
CONSIDERED_STATUSES = (AnnotationStatus.submitted,)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def majority_label(annotations: Sequence) -> tuple[list[str] | None, float, list[str]]:
    """Pick the modal `colposcopic_impression` (exact set match). Ties broken by mean confidence (higher wins).

    Returns (label, agreement_score, derived_from_ids). agreement_score is the share of
    annotators that picked the chosen label, in [0, 1]. None when the input is empty
    or no annotation has a label.
    """
    labels = [tuple(sorted(a.colposcopic_impression)) for a in annotations if a.colposcopic_impression]
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
        def mean_conf(lbl: tuple) -> float:
            confs = [a.confidence for a in annotations
                     if a.colposcopic_impression and tuple(sorted(a.colposcopic_impression)) == lbl and a.confidence is not None]
            return sum(confs) / len(confs) if confs else 0.0
        winner = max(tied, key=mean_conf)

    agreement = top_count / len(labels)
    derived = [a.id for a in annotations if a.colposcopic_impression and tuple(sorted(a.colposcopic_impression)) == winner]
    return list(winner), agreement, derived


def percent_agreement(annotations: Sequence) -> float | None:
    """Across all pairs of annotators, the fraction that agree on `colposcopic_impression` (exact match).

    Returns None if fewer than 2 annotators have labelled this patient.
    """
    labels = [tuple(sorted(a.colposcopic_impression)) for a in annotations if a.colposcopic_impression]
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


def pairwise_kappa(subject_label_map: dict[str, dict[str, tuple]]) -> float | None:
    """Average pairwise Cohen's kappa across all (rater_i, rater_j) pairs.

    `subject_label_map` maps annotator_id -> {subject_key: tuple(sorted_labels)}, where the
    subject key is a patient_code. We compute kappa only on subjects both raters have
    labelled, then average across pairs.
    """
    raters = list(subject_label_map.keys())
    if len(raters) < 2:
        return None
    kappas: list[float] = []
    for a, b in combinations(raters, 2):
        common = sorted(set(subject_label_map[a]) & set(subject_label_map[b]))
        if len(common) < 2:
            continue
        la = [subject_label_map[a][s] for s in common]
        lb = [subject_label_map[b][s] for s in common]
        k = cohen_kappa(la, lb)
        if k is not None:
            kappas.append(k)
    if not kappas:
        return None
    return sum(kappas) / len(kappas)


# ---------- DB orchestration ----------

def submitted_for_patient(patient_code: str) -> list[PatientDiagnosis]:
    return db.session.execute(
        select(PatientDiagnosis).where(and_(
            PatientDiagnosis.patient_code == patient_code,
            PatientDiagnosis.status.in_(CONSIDERED_STATUSES),
        ))
    ).scalars().all()


def upsert_consensus_for_patient(patient_code: str) -> PatientConsensus | None:
    """Recompute and persist the consensus row for one patient. Returns the row or None
    (when there's nothing to consensus over)."""
    diagnoses = submitted_for_patient(patient_code)
    label, agreement, derived = majority_label(diagnoses)
    if label is None or len(diagnoses) < 2:
        # Wipe stale consensus if requirements no longer met.
        existing = db.session.execute(
            select(PatientConsensus).where(PatientConsensus.patient_code == patient_code)
        ).scalar_one_or_none()
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()
        return None

    existing = db.session.execute(
        select(PatientConsensus).where(PatientConsensus.patient_code == patient_code)
    ).scalar_one_or_none()
    if existing is None:
        existing = PatientConsensus(patient_code=patient_code)
        db.session.add(existing)
    existing.label = label
    existing.derived_from = derived
    existing.agreement_score = agreement
    existing.computed_at = _utcnow()
    db.session.commit()
    return existing


def recompute_all() -> dict[str, int]:
    """Walk every patient that has 2+ submitted diagnoses and refresh consensus."""
    patient_codes = [r[0] for r in db.session.execute(
        select(PatientDiagnosis.patient_code, db.func.count(PatientDiagnosis.id))
        .where(PatientDiagnosis.status.in_(CONSIDERED_STATUSES))
        .group_by(PatientDiagnosis.patient_code)
        .having(db.func.count(PatientDiagnosis.id) >= 2)
    ).all()]
    updated = 0
    for code in patient_codes:
        if upsert_consensus_for_patient(code) is not None:
            updated += 1
    return {'eligible_patients': len(patient_codes), 'consensus_written': updated}


def find_disagreement_patients() -> list[dict]:
    """Patients with ≥2 submitted diagnoses whose `colposcopic_impression` doesn't match."""
    rows = db.session.execute(
        select(PatientDiagnosis.patient_code)
        .where(PatientDiagnosis.status.in_(CONSIDERED_STATUSES))
        .group_by(PatientDiagnosis.patient_code)
        .having(db.func.count(PatientDiagnosis.id) >= 2)
    ).all()
    out = []
    for (code,) in rows:
        diagnoses = submitted_for_patient(code)
        labels = {tuple(sorted(d.colposcopic_impression)) for d in diagnoses if d.colposcopic_impression}
        if len(labels) > 1:
            out.append({
                'patient_code': code,
                'labels': [list(l) for l in labels],
                'annotator_count': len(diagnoses),
                'agreement': percent_agreement(diagnoses),
            })
    return out
