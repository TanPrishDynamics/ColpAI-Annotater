"""CSV exporters.

Two levels:

- ``image``  - one row per exported image (image facts + the chosen
  annotation's Layer B fields + region counts). Good for classification.
- ``region`` - one row per region (image + region Layer C fields). Good for
  per-lesion analysis and detection sanity checks.
"""
from __future__ import annotations

import csv
import io

from app.services.exporters.selection import ExportSelection

IMAGE_COLUMNS = [
    'image_id', 'sha256', 'dataset_source', 'source_path',
    'image_phase', 'magnification_level', 'capture_device',
    'width_px', 'height_px',
    'annotation_id', 'annotator_id', 'status', 'version',
    'image_quality', 'blur_present', 'blood_present', 'mucus_present',
    'specular_reflection_present', 'lighting_issue', 'usable_for_training',
    'scj_visibility', 'transformation_zone_type', 'tz_visibility',
    'acetowhitening_severity', 'iodine_pattern', 'vascular_pattern',
    'color_tone', 'surface_contour', 'atypical_vessels_present',
    'colposcopic_impression', 'histopathology_result', 'confidence', 'notes',
    'reid_margin', 'reid_color', 'reid_vessels', 'reid_iodine', 'reid_total',
    'swede_aceto', 'swede_margin', 'swede_vessels', 'swede_size', 'swede_iodine', 'swede_total',
    'region_count', 'submitted_at',
]

REGION_COLUMNS = [
    'image_id', 'dataset_source', 'source_path', 'width_px', 'height_px',
    'annotation_id', 'annotator_id', 'colposcopic_impression',
    'region_id', 'region_type', 'lesion_label', 'lesion_location_clock',
    'lesion_quadrant', 'lesion_size_percent', 'lesion_margins',
    'punctation_present', 'punctation_severity',
    'mosaic_present', 'mosaic_severity', 'region_notes',
]


def _enum(value):
    return value.value if value is not None and hasattr(value, 'value') else value


def export_image_csv(selection: ExportSelection) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=IMAGE_COLUMNS, extrasaction='ignore')
    writer.writeheader()
    for image, ann in selection.pairs:
        writer.writerow({
            'image_id': image.id,
            'sha256': image.sha256,
            'dataset_source': image.dataset_source,
            'source_path': image.source_path,
            'image_phase': _enum(image.image_phase),
            'magnification_level': _enum(image.magnification_level),
            'capture_device': image.capture_device,
            'width_px': image.width_px,
            'height_px': image.height_px,
            'annotation_id': ann.id,
            'annotator_id': ann.annotator_id,
            'status': _enum(ann.status),
            'version': ann.version,
            'image_quality': _enum(ann.image_quality),
            'blur_present': ann.blur_present,
            'blood_present': ann.blood_present,
            'mucus_present': ann.mucus_present,
            'specular_reflection_present': ann.specular_reflection_present,
            'lighting_issue': _enum(ann.lighting_issue),
            'usable_for_training': ann.usable_for_training,
            'scj_visibility': _enum(ann.scj_visibility),
            'transformation_zone_type': _enum(ann.transformation_zone_type),
            'tz_visibility': _enum(ann.tz_visibility),
            'acetowhitening_severity': ann.acetowhitening_severity,
            'iodine_pattern': ann.iodine_pattern,
            'vascular_pattern': _enum(ann.vascular_pattern),
            'color_tone': _enum(ann.color_tone),
            'surface_contour': _enum(ann.surface_contour),
            'atypical_vessels_present': ann.atypical_vessels_present,
            'colposcopic_impression': _enum(ann.colposcopic_impression),
            'histopathology_result': _enum(ann.histopathology_result),
            'confidence': ann.confidence,
            'notes': ann.notes,
            'reid_margin': ann.reid_margin,
            'reid_color': ann.reid_color,
            'reid_vessels': ann.reid_vessels,
            'reid_iodine': ann.reid_iodine,
            'reid_total': ann.reid_total,
            'swede_aceto': ann.swede_aceto,
            'swede_margin': ann.swede_margin,
            'swede_vessels': ann.swede_vessels,
            'swede_size': ann.swede_size,
            'swede_iodine': ann.swede_iodine,
            'swede_total': ann.swede_total,
            'region_count': len(ann.regions),
            'submitted_at': ann.submitted_at.isoformat() if ann.submitted_at else None,
        })
    return buf.getvalue()


def export_region_csv(selection: ExportSelection) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=REGION_COLUMNS, extrasaction='ignore')
    writer.writeheader()
    for image, ann in selection.pairs:
        for region in ann.regions:
            writer.writerow({
                'image_id': image.id,
                'dataset_source': image.dataset_source,
                'source_path': image.source_path,
                'width_px': image.width_px,
                'height_px': image.height_px,
                'annotation_id': ann.id,
                'annotator_id': ann.annotator_id,
                'colposcopic_impression': _enum(ann.colposcopic_impression),
                'region_id': region.id,
                'region_type': _enum(region.region_type),
                'lesion_label': _enum(region.lesion_label),
                'lesion_location_clock': region.lesion_location_clock,
                'lesion_quadrant': _enum(region.lesion_quadrant),
                'lesion_size_percent': region.lesion_size_percent,
                'lesion_margins': _enum(region.lesion_margins),
                'punctation_present': region.punctation_present,
                'punctation_severity': region.punctation_severity,
                'mosaic_present': region.mosaic_present,
                'mosaic_severity': region.mosaic_severity,
                'region_notes': region.region_notes,
            })
    return buf.getvalue()
