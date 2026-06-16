"""Decide *what* gets exported, once, for every format.

Exporters must agree on the set of images and the single annotation chosen per
image, otherwise a COCO file and a CSV generated from the same request could
disagree. This module is the single source of truth for that decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from app.extensions import db
from app.models import Image, ImageAnnotation
from app.models.enums import AnnotationStatus, DiagnosisLabel

# Statuses eligible for export, grouped by the `status` request parameter.
# `draft` and `superseded` are never exported.
STATUS_FILTERS: dict[str, tuple[AnnotationStatus, ...]] = {
    'reviewed': (AnnotationStatus.reviewed, AnnotationStatus.consensus),
    'submitted': (
        AnnotationStatus.submitted,
        AnnotationStatus.reviewed,
        AnnotationStatus.consensus,
    ),
    'all': (
        AnnotationStatus.submitted,
        AnnotationStatus.reviewed,
        AnnotationStatus.consensus,
    ),
}

# How "final" a status is, used to pick the best annotation when an image has
# several. Higher wins; version and recency break ties.
_STATUS_RANK = {
    AnnotationStatus.submitted: 0,
    AnnotationStatus.reviewed: 1,
    AnnotationStatus.consensus: 2,
}

# Stable category ordering shared by COCO + YOLO so class ids never drift.
CATEGORY_ORDER: list[DiagnosisLabel] = [
    DiagnosisLabel.NORMAL,
    DiagnosisLabel.CIN1,
    DiagnosisLabel.CIN2,
    DiagnosisLabel.CIN3,
    DiagnosisLabel.AIS,
    DiagnosisLabel.INVASIVE_CANCER,
    # Appended (not reordered) so existing class ids stay stable.
    DiagnosisLabel.INFLAMMATION,
    DiagnosisLabel.INFECTION,
    DiagnosisLabel.EROSION,
]


@dataclass
class ExportSelection:
    """One chosen annotation per image, plus the fixed category list."""

    pairs: list[tuple[Image, ImageAnnotation]] = field(default_factory=list)
    status_filter: str = 'reviewed'
    dataset_source: str | None = None

    @property
    def categories(self) -> list[DiagnosisLabel]:
        return list(CATEGORY_ORDER)

    def category_index(self, label: DiagnosisLabel) -> int:
        return CATEGORY_ORDER.index(label)

    def __len__(self) -> int:
        return len(self.pairs)


def _annotation_sort_key(ann: ImageAnnotation) -> tuple:
    return (
        _STATUS_RANK.get(ann.status, -1),
        ann.version or 0,
        ann.submitted_at or ann.updated_at or ann.created_at,
    )


def gather_export_selection(
    dataset_source: str | None = None,
    status: str = 'reviewed',
) -> ExportSelection:
    """Pick the single best annotation per image for the requested status set.

    "Best" = most-final status, then highest version, then most recent. This
    keeps multi-annotator / multi-version images deterministic across formats.
    """
    if status not in STATUS_FILTERS:
        raise ValueError(
            f"Unknown status '{status}'. Expected one of {sorted(STATUS_FILTERS)}."
        )

    eligible = STATUS_FILTERS[status]

    stmt = (
        select(ImageAnnotation)
        .join(Image, ImageAnnotation.image_id == Image.id)
        .where(ImageAnnotation.status.in_(eligible))
    )
    if dataset_source:
        stmt = stmt.where(Image.dataset_source == dataset_source)

    annotations = db.session.execute(stmt).scalars().all()

    best_by_image: dict[str, ImageAnnotation] = {}
    for ann in annotations:
        current = best_by_image.get(ann.image_id)
        if current is None or _annotation_sort_key(ann) > _annotation_sort_key(current):
            best_by_image[ann.image_id] = ann

    pairs: list[tuple[Image, ImageAnnotation]] = []
    for ann in best_by_image.values():
        pairs.append((ann.image, ann))

    # Deterministic output order: by image id.
    pairs.sort(key=lambda p: p[0].id)

    return ExportSelection(
        pairs=pairs,
        status_filter=status,
        dataset_source=dataset_source,
    )
