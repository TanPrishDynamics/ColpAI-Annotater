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
    annotator here (not per photo) -- like colposcopic_impression, they're
    optional on autosave and never required to submit.
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


class PatientDiagnosisSubmit(PatientDiagnosisPatch):
    """Body for POST /patients/{code}/diagnosis/submit. Validated against the merged row."""

    @model_validator(mode='after')
    def _diagnosis_required(self):
        if not self.colposcopic_impression or self.confidence is None:
            raise ValueError(
                'colposcopic_impression and confidence are required to submit a patient diagnosis.'
            )
        return self
