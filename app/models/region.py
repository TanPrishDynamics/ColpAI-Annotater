"""Region model. Per-lesion annotations attached to an ImageAnnotation."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import Enum as SAEnum

from app.extensions import db
from app.models.enums import (
    RegionType,
    DiagnosisLabel,
    LesionMargins,
    LesionQuadrant,
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Region(db.Model):
    __tablename__ = 'regions'

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    image_annotation_id = db.Column(
        db.String(36),
        db.ForeignKey('image_annotations.id'),
        nullable=False,
        index=True,
    )

    region_type = db.Column(SAEnum(RegionType, name='region_type'), nullable=False)
    geometry = db.Column(db.JSON, nullable=False)

    lesion_label = db.Column(SAEnum(DiagnosisLabel, name='lesion_label'), nullable=True)
    lesion_location_clock = db.Column(db.Integer, nullable=True)
    lesion_quadrant = db.Column(SAEnum(LesionQuadrant, name='lesion_quadrant'), nullable=True)
    lesion_size_percent = db.Column(db.Integer, nullable=True)
    lesion_margins = db.Column(SAEnum(LesionMargins, name='lesion_margins'), nullable=True)

    punctation_present = db.Column(db.Boolean, nullable=True)
    punctation_severity = db.Column(db.Integer, nullable=True)
    mosaic_present = db.Column(db.Boolean, nullable=True)
    mosaic_severity = db.Column(db.Integer, nullable=True)

    region_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    annotation = db.relationship('ImageAnnotation', back_populates='regions')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'image_annotation_id': self.image_annotation_id,
            'region_type': self.region_type.value,
            'geometry': self.geometry,
            'lesion_label': self.lesion_label.value if self.lesion_label else None,
            'lesion_location_clock': self.lesion_location_clock,
            'lesion_quadrant': self.lesion_quadrant.value if self.lesion_quadrant else None,
            'lesion_size_percent': self.lesion_size_percent,
            'lesion_margins': self.lesion_margins.value if self.lesion_margins else None,
            'punctation_present': self.punctation_present,
            'punctation_severity': self.punctation_severity,
            'mosaic_present': self.mosaic_present,
            'mosaic_severity': self.mosaic_severity,
            'region_notes': self.region_notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
