"""Pydantic schemas for image-level annotation endpoints (Phase 2).

Region (Layer C) creation is Phase 3; we expose `regions` as read-only here so the
annotate UI can still display existing regions on revisit.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    AnnotationStatus,
    ColorTone,
    DiagnosisLabel,
    ImageQuality,
    LightingIssue,
    SCJVisibility,
    SurfaceContour,
    TZType,
    TZVisibility,
    VascularPattern,
)


class QualityBlock(BaseModel):
    image_quality: ImageQuality | None = None
    blur_present: bool | None = None
    blood_present: bool | None = None
    mucus_present: bool | None = None
    specular_reflection_present: bool | None = None
    lighting_issue: LightingIssue | None = None
    usable_for_training: bool | None = None


class AnatomyBlock(BaseModel):
    scj_visibility: SCJVisibility | None = None
    transformation_zone_type: TZType | None = None
    tz_visibility: TZVisibility | None = None


class FeaturesBlock(BaseModel):
    acetowhitening_severity: int | None = Field(default=None, ge=0, le=3)
    iodine_pattern: int | None = Field(default=None, ge=0, le=2)
    vascular_pattern: VascularPattern | None = None
    color_tone: ColorTone | None = None
    surface_contour: SurfaceContour | None = None
    atypical_vessels_present: bool | None = None


class DiagnosisBlock(BaseModel):
    colposcopic_impression: DiagnosisLabel | None = None
    histopathology_result: DiagnosisLabel | None = None
    confidence: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=4000)


class CropBox(BaseModel):
    """The annotator's crop rectangle, in image pixel coordinates.

    A zero-area box (w or h == 0) is treated as a request to clear the crop.
    """
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=0)
    h: int = Field(ge=0)


class AnnotationCreate(BaseModel):
    """Body for POST /annotations. The server fills annotator/version/status."""
    image_id: str = Field(min_length=1, max_length=64)


class AnnotationPatch(BaseModel):
    """Autosave body. Every block is optional; partial blocks are allowed."""
    quality: QualityBlock | None = None
    anatomy: AnatomyBlock | None = None
    features: FeaturesBlock | None = None
    diagnosis: DiagnosisBlock | None = None
    crop_box: CropBox | None = None


class AnnotationSubmit(BaseModel):
    """Body for POST /annotations/{id}/submit. Server-side validation lives in the view."""
    quality: QualityBlock | None = None
    anatomy: AnatomyBlock | None = None
    features: FeaturesBlock | None = None
    diagnosis: DiagnosisBlock | None = None
    crop_box: CropBox | None = None

    @model_validator(mode='after')
    def _diagnosis_required(self):
        d = self.diagnosis
        if d is None or d.colposcopic_impression is None or d.confidence is None:
            raise ValueError(
                'diagnosis.colposcopic_impression and diagnosis.confidence are required to submit.'
            )
        return self


class DiscardRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class AnnotationListQuery(BaseModel):
    image_id: str | None = None
    annotator_id: str | None = None
    status: AnnotationStatus | None = None
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = None
