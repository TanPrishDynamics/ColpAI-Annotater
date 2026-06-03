"""Pydantic schema for export query parameters."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExportQuery(BaseModel):
    """Common query params for every export format.

    - ``dataset`` limits to a single ``dataset_source`` (omit for everything).
    - ``status`` controls which annotations qualify:
        ``reviewed``  (default) only reviewed/consensus,
        ``submitted`` submitted and better,
        ``all``       any non-draft, non-superseded annotation.
    - ``level`` (CSV only) picks per-image vs per-region rows.
    """

    dataset: str | None = Field(default=None, max_length=128)
    status: Literal['reviewed', 'submitted', 'all'] = 'reviewed'
    level: Literal['image', 'region'] = 'image'
