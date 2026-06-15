"""Pydantic schemas for image queue endpoints."""
from pydantic import BaseModel, Field


class ImageQueueQuery(BaseModel):
    """Query params for GET /api/v1/images. All optional."""
    phase: str | None = None
    dataset_source: str | None = None
    status: str | None = Field(default=None, description="unannotated | mine | reviewed | all")
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = None


class ImageOut(BaseModel):
    id: str
    sha256: str
    source_path: str
    dataset_source: str
    patient_code: str | None = None
    image_phase: str | None = None
    capture_device: str | None = None
    magnification_level: str | None = None
    width_px: int | None = None
    height_px: int | None = None
    image_resolution: str | None = None
    file_size_bytes: int | None = None
    ingested_at: str | None = None


class ImageQueueResponse(BaseModel):
    items: list[ImageOut]
    next_cursor: str | None = None
    total: int | None = None
