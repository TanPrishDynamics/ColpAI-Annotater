"""Which annotation fields are compulsory, and whether a row has them all.

Training data is only useful when every exported row carries the same columns,
so an ImageAnnotation cannot be finalized (draft -> submitted) until every field
listed here is filled. This module is the single source of truth: the finalize
paths in app/api/patients.py and app/api/annotations.py, the per-image
completeness the diagnose page displays, and the annotate UI's own indicator
all derive from it.

Regions are optional (a normal cervix has none), but every region that IS drawn
must be fully described: the COCO/YOLO/mask exporters key a region's class on
its lesion_label and silently skip unlabeled ones, so a half-filled region is a
lesion the model never sees.

Deliberately NOT required:
- The Quality block (image_quality, blur/blood/mucus/specular, lighting_issue,
  usable_for_training): only reviewers/admins see it on the form, so annotators
  could never satisfy it.
- notes / region_notes / crop_box: free text and optional geometry.
"""
from __future__ import annotations

import re

from app.models import Image, ImageAnnotation, Region

# Block -> columns, in form order. The dotted "block.field" path matches the
# `data-field` attribute on the annotate form so the UI can point at the control.
REQUIRED_ANNOTATION_FIELDS: dict[str, tuple[str, ...]] = {
    'anatomy': ('scj_visibility', 'transformation_zone_type', 'tz_visibility'),
    'assessment': ('ifcpc_grade', 'colposcopy_adequacy'),
    'features': (
        'acetowhitening_severity', 'iodine_pattern', 'vascular_pattern',
        'color_tone', 'surface_contour', 'atypical_vessels_present',
    ),
}

# Checkbox-backed booleans. An untouched box means "absent", never "unknown", so
# a NULL here is normalized to False rather than reported as missing.
CHECKBOX_FIELDS: tuple[str, ...] = ('atypical_vessels_present',)

# Per-region requirements. Paths are reported as "regions[<n>].<field>" with n
# the 1-based position in the region list (creation order), matching the "#n"
# the annotate page shows.
REQUIRED_REGION_FIELDS: tuple[str, ...] = (
    'lesion_label', 'lesion_location_clock', 'lesion_quadrant', 'lesion_margins',
)
REGION_CHECKBOX_FIELDS: tuple[str, ...] = ('punctation_present', 'mosaic_present')
# A severity is only meaningful (and only required) when its feature is present.
REGION_CONDITIONAL_FIELDS: dict[str, str] = {
    'punctation_severity': 'punctation_present',
    'mosaic_severity': 'mosaic_present',
}

# Human-readable names for error messages, keyed by dotted path.
FIELD_LABELS: dict[str, str] = {
    'image_type': 'Image type',
    'anatomy.scj_visibility': 'SCJ visibility',
    'anatomy.transformation_zone_type': 'TZ type',
    'anatomy.tz_visibility': 'TZ visibility',
    'assessment.ifcpc_grade': 'Colposcopic finding grade',
    'assessment.colposcopy_adequacy': 'Colposcopy adequacy',
    'features.acetowhitening_severity': 'Acetowhitening',
    'features.iodine_pattern': 'Iodine',
    'features.vascular_pattern': 'Vascular pattern',
    'features.color_tone': 'Color tone',
    'features.surface_contour': 'Surface contour',
    'features.atypical_vessels_present': 'Atypical vessels present',
    'region.lesion_label': 'Lesion label',
    'region.lesion_location_clock': 'Clock',
    'region.lesion_quadrant': 'Quadrant',
    'region.lesion_margins': 'Margins',
    'region.punctation_severity': 'Punctation severity',
    'region.mosaic_severity': 'Mosaic severity',
}

_REGION_PATH = re.compile(r'^regions\[(\d+)\]\.(.+)$')


def ordered_regions(ann: ImageAnnotation) -> list[Region]:
    """Regions in the order the annotate page numbers them (creation order)."""
    return sorted(ann.regions, key=lambda r: (r.created_at or 0, r.id))


def normalize_checkboxes(ann: ImageAnnotation) -> None:
    """Coerce NULL checkbox booleans to False, in place (not committed)."""
    for field in CHECKBOX_FIELDS:
        if getattr(ann, field) is None:
            setattr(ann, field, False)
    for region in ann.regions:
        for field in REGION_CHECKBOX_FIELDS:
            if getattr(region, field) is None:
                setattr(region, field, False)


def missing_region_fields(region: Region) -> list[str]:
    """Bare field names still unset on one region."""
    missing = [f for f in REQUIRED_REGION_FIELDS if getattr(region, f) is None]
    for field, gate in REGION_CONDITIONAL_FIELDS.items():
        if getattr(region, gate) and getattr(region, field) is None:
            missing.append(field)
    return missing


def missing_fields(ann: ImageAnnotation, image: Image | None = None) -> list[str]:
    """Dotted paths of every required field still unset on `ann`.

    `image` (defaults to ann.image) contributes `image_type`: the phase lives on
    the shared Image row, not the annotation, but it's set from the same form and
    a photo whose phase is unknown is just as useless for training.
    """
    img = image if image is not None else ann.image
    missing: list[str] = []
    if img is None or img.image_phase is None:
        missing.append('image_type')
    for block, fields in REQUIRED_ANNOTATION_FIELDS.items():
        for field in fields:
            if field in CHECKBOX_FIELDS:
                continue
            if getattr(ann, field) is None:
                missing.append(f'{block}.{field}')
    for n, region in enumerate(ordered_regions(ann), start=1):
        for field in missing_region_fields(region):
            missing.append(f'regions[{n}].{field}')
    return missing


def describe_missing(paths: list[str]) -> str:
    """'Image type, TZ type; Region 1: Lesion label, Clock; Region 2: Quadrant'."""
    image_level: list[str] = []
    per_region: dict[int, list[str]] = {}
    for path in paths:
        m = _REGION_PATH.match(path)
        if m:
            n, field = int(m.group(1)), m.group(2)
            per_region.setdefault(n, []).append(FIELD_LABELS.get(f'region.{field}', field))
        else:
            image_level.append(FIELD_LABELS.get(path, path))
    parts = []
    if image_level:
        parts.append(', '.join(image_level))
    for n in sorted(per_region):
        parts.append(f'Region {n}: ' + ', '.join(per_region[n]))
    return '; '.join(parts)
