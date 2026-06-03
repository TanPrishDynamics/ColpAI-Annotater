"""Pydantic schemas for review endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ReviewActionBody(BaseModel):
    """approve/reject body. Comment is optional but recommended for reject."""
    comment: str | None = Field(default=None, max_length=4000)


class ReviewQueueQuery(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = None
    annotator_id: str | None = None
    image_id: str | None = None
