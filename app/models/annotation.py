"""Image-level annotation. One row per (image, annotator, version)."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import Enum as SAEnum, UniqueConstraint, Index

from app.extensions import db
from app.models.enums import (
    AnnotationStatus,
    ImageQuality,
    LightingIssue,
    SCJVisibility,
    TZType,
    TZVisibility,
    VascularPattern,
    ColorTone,
    SurfaceContour,
    DiagnosisLabel,
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ImageAnnotation(db.Model):
    __tablename__ = 'image_annotations'
    __table_args__ = (
        UniqueConstraint('image_id', 'annotator_id', 'version', name='uq_annotation_version'),
        Index('ix_annotation_image_status', 'image_id', 'status'),
        Index('ix_annotation_annotator_status', 'annotator_id', 'status'),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)

    image_id = db.Column(db.String(36), db.ForeignKey('images.id'), nullable=False, index=True)
    annotator_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False, index=True)
    status = db.Column(
        SAEnum(AnnotationStatus, name='annotation_status'),
        nullable=False,
        default=AnnotationStatus.draft,
    )
    version = db.Column(db.Integer, nullable=False, default=1)

    image_quality = db.Column(SAEnum(ImageQuality, name='image_quality'), nullable=True)
    blur_present = db.Column(db.Boolean, nullable=True)
    blood_present = db.Column(db.Boolean, nullable=True)
    mucus_present = db.Column(db.Boolean, nullable=True)
    specular_reflection_present = db.Column(db.Boolean, nullable=True)
    lighting_issue = db.Column(SAEnum(LightingIssue, name='lighting_issue'), nullable=True)
    usable_for_training = db.Column(db.Boolean, nullable=True)

    scj_visibility = db.Column(SAEnum(SCJVisibility, name='scj_visibility'), nullable=True)
    transformation_zone_type = db.Column(SAEnum(TZType, name='tz_type'), nullable=True)
    tz_visibility = db.Column(SAEnum(TZVisibility, name='tz_visibility'), nullable=True)

    acetowhitening_severity = db.Column(db.Integer, nullable=True)
    iodine_pattern = db.Column(db.Integer, nullable=True)
    vascular_pattern = db.Column(SAEnum(VascularPattern, name='vascular_pattern'), nullable=True)
    color_tone = db.Column(SAEnum(ColorTone, name='color_tone'), nullable=True)
    surface_contour = db.Column(SAEnum(SurfaceContour, name='surface_contour'), nullable=True)
    atypical_vessels_present = db.Column(db.Boolean, nullable=True)

    colposcopic_impression = db.Column(SAEnum(DiagnosisLabel, name='diagnosis_label'), nullable=True)
    histopathology_result = db.Column(
        SAEnum(DiagnosisLabel, name='diagnosis_label_histo'),
        nullable=True,
    )
    confidence = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    # Colposcopic scoring indices. Each criterion is graded 0/1/2; totals are
    # derived (see reid_total / swede_total). Reid Colposcopic Index (RCI, 0-8)
    # and Swede score (0-10).
    reid_margin = db.Column(db.Integer, nullable=True)
    reid_color = db.Column(db.Integer, nullable=True)
    reid_vessels = db.Column(db.Integer, nullable=True)
    reid_iodine = db.Column(db.Integer, nullable=True)

    swede_aceto = db.Column(db.Integer, nullable=True)
    swede_margin = db.Column(db.Integer, nullable=True)
    swede_vessels = db.Column(db.Integer, nullable=True)
    swede_size = db.Column(db.Integer, nullable=True)
    swede_iodine = db.Column(db.Integer, nullable=True)

    # Optional crop region the annotator drew (image pixel coords: {x, y, w, h}).
    # On reviewer approval, the final annotated image is rendered and stored under
    # annotated/<patient>/; crop_path is its storage reference (resolved through
    # app.services.storage, same as Image.source_path). It stays unset for drafts
    # and submitted-but-unreviewed annotations.
    crop_box = db.Column(db.JSON, nullable=True)
    crop_path = db.Column(db.String(1024), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    image = db.relationship('Image', back_populates='annotations')
    annotator = db.relationship('User', back_populates='annotations')
    regions = db.relationship(
        'Region',
        back_populates='annotation',
        cascade='all, delete-orphan',
        lazy='select',
    )
    review_actions = db.relationship(
        'ReviewAction',
        back_populates='annotation',
        cascade='all, delete-orphan',
        lazy='dynamic',
    )

    @property
    def reid_total(self) -> int | None:
        """Reid Colposcopic Index total (0-8), or None until all 4 criteria are scored."""
        parts = (self.reid_margin, self.reid_color, self.reid_vessels, self.reid_iodine)
        return sum(parts) if all(p is not None for p in parts) else None

    @property
    def swede_total(self) -> int | None:
        """Swede score total (0-10), or None until all 5 criteria are scored."""
        parts = (self.swede_aceto, self.swede_margin, self.swede_vessels,
                 self.swede_size, self.swede_iodine)
        return sum(parts) if all(p is not None for p in parts) else None

    def to_dict(self, include_regions: bool = False) -> dict:
        out = {
            'id': self.id,
            'image_id': self.image_id,
            'annotator_id': self.annotator_id,
            'status': self.status.value,
            'version': self.version,
            'quality': {
                'image_quality': self.image_quality.value if self.image_quality else None,
                'blur_present': self.blur_present,
                'blood_present': self.blood_present,
                'mucus_present': self.mucus_present,
                'specular_reflection_present': self.specular_reflection_present,
                'lighting_issue': self.lighting_issue.value if self.lighting_issue else None,
                'usable_for_training': self.usable_for_training,
            },
            'anatomy': {
                'scj_visibility': self.scj_visibility.value if self.scj_visibility else None,
                'transformation_zone_type': self.transformation_zone_type.value if self.transformation_zone_type else None,
                'tz_visibility': self.tz_visibility.value if self.tz_visibility else None,
            },
            'features': {
                'acetowhitening_severity': self.acetowhitening_severity,
                'iodine_pattern': self.iodine_pattern,
                'vascular_pattern': self.vascular_pattern.value if self.vascular_pattern else None,
                'color_tone': self.color_tone.value if self.color_tone else None,
                'surface_contour': self.surface_contour.value if self.surface_contour else None,
                'atypical_vessels_present': self.atypical_vessels_present,
            },
            'diagnosis': {
                'colposcopic_impression': self.colposcopic_impression.value if self.colposcopic_impression else None,
                'histopathology_result': self.histopathology_result.value if self.histopathology_result else None,
                'confidence': self.confidence,
                'notes': self.notes,
            },
            'scoring': {
                'reid_margin': self.reid_margin,
                'reid_color': self.reid_color,
                'reid_vessels': self.reid_vessels,
                'reid_iodine': self.reid_iodine,
                'reid_total': self.reid_total,
                'swede_aceto': self.swede_aceto,
                'swede_margin': self.swede_margin,
                'swede_vessels': self.swede_vessels,
                'swede_size': self.swede_size,
                'swede_iodine': self.swede_iodine,
                'swede_total': self.swede_total,
            },
            'crop_box': self.crop_box,
            'has_crop_image': bool(self.crop_path),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
        }
        if include_regions:
            out['regions'] = [r.to_dict() for r in self.regions]
        return out
