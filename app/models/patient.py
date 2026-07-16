"""Patient-level diagnosis + consensus.

The FINAL diagnosis belongs to a patient (case), not to any single image. Each
annotator records at most one `PatientDiagnosis` per patient_code; consensus is
computed across annotators into `PatientConsensus`.

Patient identity is `Image.patient_code` (PAT-NNN), so there is no patients table
and no FK -- codeless images simply have no patient diagnosis.
"""
from datetime import datetime, timezone
import uuid

from sqlalchemy import Enum as SAEnum, UniqueConstraint, Index

from app.extensions import db
from app.models.enums import (
    AnnotationStatus,
    CytologyResult,
    DiagnosisLabel,
    HPVStatus,
    ManagementRecommendation,
)


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PatientDiagnosis(db.Model):
    """One annotator's final diagnosis for one patient. Only draft/submitted are used
    (no review flow); a submitted diagnosis is immutable via the API."""
    __tablename__ = 'patient_diagnoses'
    __table_args__ = (
        UniqueConstraint('patient_code', 'annotator_id', name='uq_patient_diagnosis_annotator'),
        Index('ix_patient_diagnosis_code_status', 'patient_code', 'status'),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    patient_code = db.Column(db.String(32), nullable=False, index=True)
    annotator_id = db.Column(db.String(36), db.ForeignKey('users.id'), nullable=False, index=True)
    status = db.Column(
        SAEnum(AnnotationStatus, name='annotation_status'),
        nullable=False,
        default=AnnotationStatus.draft,
    )

    colposcopic_impression = db.Column(db.JSON, nullable=True)  # list of DiagnosisLabel strings
    histopathology_result = db.Column(
        SAEnum(DiagnosisLabel, name='diagnosis_label_histo'),
        nullable=True,
    )
    confidence = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    # Screening context / clinical outcome (ground truth for the case).
    cytology_result = db.Column(SAEnum(CytologyResult, name='cytology_result'), nullable=True)
    hpv_status = db.Column(SAEnum(HPVStatus, name='hpv_status'), nullable=True)
    management_recommendation = db.Column(
        SAEnum(ManagementRecommendation, name='management_recommendation'),
        nullable=True,
    )
    biopsy_taken = db.Column(db.Boolean, nullable=True)

    # Colposcopic scoring indices for the whole case (one score per annotator per
    # patient, not per photo). Each criterion is graded 0/1/2; totals are derived
    # (see reid_total / swede_total). Reid Colposcopic Index (RCI, 0-8) and Swede
    # score (0-10).
    reid_margin = db.Column(db.Integer, nullable=True)
    reid_color = db.Column(db.Integer, nullable=True)
    reid_vessels = db.Column(db.Integer, nullable=True)
    reid_iodine = db.Column(db.Integer, nullable=True)

    swede_aceto = db.Column(db.Integer, nullable=True)
    swede_margin = db.Column(db.Integer, nullable=True)
    swede_vessels = db.Column(db.Integer, nullable=True)
    swede_size = db.Column(db.Integer, nullable=True)
    swede_iodine = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    annotator = db.relationship('User')

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

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'patient_code': self.patient_code,
            'annotator_id': self.annotator_id,
            'status': self.status.value,
            'colposcopic_impression': self.colposcopic_impression or [],
            'histopathology_result': self.histopathology_result.value if self.histopathology_result else None,
            'confidence': self.confidence,
            'notes': self.notes,
            'cytology_result': self.cytology_result.value if self.cytology_result else None,
            'hpv_status': self.hpv_status.value if self.hpv_status else None,
            'management_recommendation': self.management_recommendation.value if self.management_recommendation else None,
            'biopsy_taken': self.biopsy_taken,
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
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
        }


class PatientConsensus(db.Model):
    __tablename__ = 'patient_consensus'

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    patient_code = db.Column(db.String(32), nullable=False, unique=True)
    label = db.Column(db.JSON, nullable=False)  # list of DiagnosisLabel strings
    derived_from = db.Column(db.JSON, nullable=False)  # list of PatientDiagnosis ids
    agreement_score = db.Column(db.Float, nullable=True)
    computed_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            'patient_code': self.patient_code,
            'label': self.label,
            'derived_from': self.derived_from,
            'agreement_score': self.agreement_score,
            'computed_at': self.computed_at.isoformat() if self.computed_at else None,
        }
