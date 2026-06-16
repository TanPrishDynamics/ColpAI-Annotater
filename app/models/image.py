"""Image model. One row per unique image (deduped by sha256), regardless of where the file lives."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import Enum as SAEnum

from app.extensions import db
from app.models.enums import ImagePhase, MagnificationLevel


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Image(db.Model):
    __tablename__ = 'images'

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    sha256 = db.Column(db.String(64), unique=True, nullable=False, index=True)
    source_path = db.Column(db.String(1024), nullable=False)
    dataset_source = db.Column(db.String(128), nullable=False, index=True)

    # Patient/folder grouping. When an admin uploads a whole folder, every image
    # in it shares an auto-assigned code (PAT-001, PAT-002, ...) and is stored
    # under that prefix in the bucket. Null for individually-uploaded images.
    patient_code = db.Column(db.String(32), nullable=True, index=True)

    image_phase = db.Column(SAEnum(ImagePhase, name='image_phase'), nullable=True, index=True)
    capture_device = db.Column(db.String(128), nullable=True)
    magnification_level = db.Column(
        SAEnum(MagnificationLevel, name='magnification_level'),
        nullable=True,
    )

    width_px = db.Column(db.Integer, nullable=True)
    height_px = db.Column(db.Integer, nullable=True)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)

    ingested_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    annotations = db.relationship(
        'ImageAnnotation',
        back_populates='image',
        cascade='all, delete-orphan',
        lazy='dynamic',
    )
    consensus = db.relationship(
        'ConsensusLabel', back_populates='image', uselist=False,
        cascade='all, delete-orphan',
    )
    discards = db.relationship(
        'DiscardedImage', back_populates='image', lazy='dynamic',
        cascade='all, delete-orphan',
    )

    @property
    def image_resolution(self) -> str | None:
        if self.width_px and self.height_px:
            return f"{self.width_px}x{self.height_px}"
        return None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'sha256': self.sha256,
            'source_path': self.source_path,
            'dataset_source': self.dataset_source,
            'patient_code': self.patient_code,
            'image_phase': self.image_phase.value if self.image_phase else None,
            'capture_device': self.capture_device,
            'magnification_level': self.magnification_level.value if self.magnification_level else None,
            'width_px': self.width_px,
            'height_px': self.height_px,
            'image_resolution': self.image_resolution,
            'file_size_bytes': self.file_size_bytes,
            'ingested_at': self.ingested_at.isoformat() if self.ingested_at else None,
        }

    def __repr__(self) -> str:
        return f"<Image {self.id} sha={self.sha256[:8]} src={self.dataset_source}>"
