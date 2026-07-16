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

from app.extensions import db
from app.models import PatientConsensus, PatientDiagnosis, User
from app.models.enums import AnnotationStatus
from app.services.exporters.selection import ExportSelection

PATIENT_COLUMNS = [
    'patient_code', 'annotator_id', 'annotator_username', 'status',
    'colposcopic_impression', 'histopathology_result', 'confidence', 'notes',
    'cytology_result', 'hpv_status', 'management_recommendation', 'biopsy_taken',
    'reid_margin', 'reid_color', 'reid_vessels', 'reid_iodine', 'reid_total',
    'swede_aceto', 'swede_margin', 'swede_vessels', 'swede_size', 'swede_iodine', 'swede_total',
    'submitted_at', 'consensus_label', 'agreement_score', 'consensus_computed_at',
]

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
    'ifcpc_grade', 'colposcopy_adequacy',
    'notes', 'region_count', 'submitted_at',
]

REGION_COLUMNS = [
    'image_id', 'dataset_source', 'source_path', 'width_px', 'height_px',
    'annotation_id', 'annotator_id',
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
            'ifcpc_grade': _enum(ann.ifcpc_grade),
            'colposcopy_adequacy': _enum(ann.colposcopy_adequacy),
            'notes': ann.notes,
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


def export_patient_csv() -> str:
    """One row per submitted patient diagnosis (the FINAL diagnosis), independent of
    the per-image export selection -- there's no image/annotation to select here."""
    rows = (
        db.session.query(PatientDiagnosis, User)
        .join(User, User.id == PatientDiagnosis.annotator_id)
        .filter(PatientDiagnosis.status == AnnotationStatus.submitted)
        .order_by(PatientDiagnosis.patient_code)
        .all()
    )
    consensus_by_patient = {
        c.patient_code: c
        for c in db.session.query(PatientConsensus).all()
    }

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=PATIENT_COLUMNS, extrasaction='ignore')
    writer.writeheader()
    for diagnosis, user in rows:
        consensus = consensus_by_patient.get(diagnosis.patient_code)
        writer.writerow({
            'patient_code': diagnosis.patient_code,
            'annotator_id': diagnosis.annotator_id,
            'annotator_username': user.username,
            'status': _enum(diagnosis.status),
            'colposcopic_impression': ", ".join(diagnosis.colposcopic_impression) if diagnosis.colposcopic_impression else '',
            'histopathology_result': _enum(diagnosis.histopathology_result),
            'confidence': diagnosis.confidence,
            'notes': diagnosis.notes,
            'cytology_result': _enum(diagnosis.cytology_result),
            'hpv_status': _enum(diagnosis.hpv_status),
            'management_recommendation': _enum(diagnosis.management_recommendation),
            'biopsy_taken': diagnosis.biopsy_taken,
            'reid_margin': diagnosis.reid_margin,
            'reid_color': diagnosis.reid_color,
            'reid_vessels': diagnosis.reid_vessels,
            'reid_iodine': diagnosis.reid_iodine,
            'reid_total': diagnosis.reid_total,
            'swede_aceto': diagnosis.swede_aceto,
            'swede_margin': diagnosis.swede_margin,
            'swede_vessels': diagnosis.swede_vessels,
            'swede_size': diagnosis.swede_size,
            'swede_iodine': diagnosis.swede_iodine,
            'swede_total': diagnosis.swede_total,
            'submitted_at': diagnosis.submitted_at.isoformat() if diagnosis.submitted_at else None,
            'consensus_label': ", ".join(consensus.label) if consensus else '',
            'agreement_score': consensus.agreement_score if consensus else None,
            'consensus_computed_at': consensus.computed_at.isoformat() if consensus else None,
        })
    return buf.getvalue()
