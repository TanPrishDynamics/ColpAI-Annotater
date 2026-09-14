"""Pydantic schemas for patient-level diagnosis endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    CytologyResult,
    DiagnosisLabel,
    HPVStatus,
    ManagementRecommendation,
)


class PatientDiagnosisPatch(BaseModel):
    """Autosave body for PATCH /patients/{code}/diagnosis. Every field is optional.

    Reid Colposcopic Index and Swede Score are scored once per patient per
    annotator here (not per photo). Like colposcopic_impression and confidence
    they're optional on autosave but compulsory to submit -- see
    PatientDiagnosisSubmit. Histopathology and the screening context
    (cytology / HPV / management) stay optional: they depend on results that
    may not exist yet.
    """
    colposcopic_impression: list[DiagnosisLabel] | None = None
    histopathology_result: DiagnosisLabel | None = None
    confidence: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=4000)

    # Screening context / clinical outcome.
    cytology_result: CytologyResult | None = None
    hpv_status: HPVStatus | None = None
    management_recommendation: ManagementRecommendation | None = None
    biopsy_taken: bool | None = None

    reid_margin: int | None = Field(default=None, ge=0, le=2)
    reid_color: int | None = Field(default=None, ge=0, le=2)
    reid_vessels: int | None = Field(default=None, ge=0, le=2)
    reid_iodine: int | None = Field(default=None, ge=0, le=2)
    swede_aceto: int | None = Field(default=None, ge=0, le=2)
    swede_margin: int | None = Field(default=None, ge=0, le=2)
    swede_vessels: int | None = Field(default=None, ge=0, le=2)
    swede_size: int | None = Field(default=None, ge=0, le=2)
    swede_iodine: int | None = Field(default=None, ge=0, le=2)


REID_FIELDS: tuple[str, ...] = ('reid_margin', 'reid_color', 'reid_vessels', 'reid_iodine')
SWEDE_FIELDS: tuple[str, ...] = ('swede_aceto', 'swede_margin', 'swede_vessels', 'swede_size', 'swede_iodine')

# Everything that must be filled before a diagnosis can be submitted. Exports
# read these columns straight into the training set, so a submitted row with a
# gap here would be a row the model can't learn from consistently.
SUBMIT_REQUIRED_FIELDS: tuple[str, ...] = ('colposcopic_impression', 'confidence', *REID_FIELDS, *SWEDE_FIELDS)


class PatientDiagnosisSubmit(PatientDiagnosisPatch):
    """Body for POST /patients/{code}/diagnosis/submit. Validated against the merged row."""

    @model_validator(mode='after')
    def _diagnosis_required(self):
        missing = [
            f for f in SUBMIT_REQUIRED_FIELDS
            if getattr(self, f) is None or getattr(self, f) == []
        ]
        if missing:
            raise ValueError(
                'Required to submit a patient diagnosis: ' + ', '.join(missing) + '.'
            )
        return self
