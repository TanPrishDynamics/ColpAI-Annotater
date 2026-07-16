"""Review models. Consensus is now patient-level (see app/models/patient.py)."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import Enum as SAEnum

from app.extensions import db
from app.models.enums import ReviewActionType


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ReviewAction(db.Model):
    __tablename__ = 'review_actions'

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    image_annotation_id = db.Column(
        db.String(36),
        db.ForeignKey('image_annotations.id'),
        nullable=False,
        index=True,
    )
    reviewer_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False, index=True)
    action = db.Column(SAEnum(ReviewActionType, name='review_action'), nullable=False)
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    annotation = db.relationship('ImageAnnotation', back_populates='review_actions')
    reviewer = db.relationship('User')


class DiscardedImage(db.Model):
    __tablename__ = 'discarded_images'

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    image_id = db.Column(db.String(36), db.ForeignKey('images.id'), nullable=False, index=True)
    annotator_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    discarded_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    image = db.relationship('Image', back_populates='discards')
    annotator = db.relationship('User')
