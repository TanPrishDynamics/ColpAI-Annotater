"""Pydantic schemas for region (Layer C) endpoints."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.enums import (
    DiagnosisLabel,
    LesionMargins,
    LesionQuadrant,
    RegionType,
)


def _validate_geometry(region_type: RegionType, geometry: dict[str, Any]) -> None:
    """Per-type geometry shape checks. Pixel bounds against the parent image
    are enforced in the view layer where we have access to the image's dimensions.
    """
    if not isinstance(geometry, dict):
        raise ValueError('geometry must be a JSON object.')

    if region_type == RegionType.bbox:
        for k in ('x', 'y', 'w', 'h'):
            if k not in geometry:
                raise ValueError(f'bbox geometry missing key "{k}".')
            if not isinstance(geometry[k], (int, float)):
                raise ValueError(f'bbox geometry "{k}" must be numeric.')
        if geometry['w'] <= 0 or geometry['h'] <= 0:
            raise ValueError('bbox width and height must be positive.')

    elif region_type == RegionType.polygon:
        pts = geometry.get('points')
        if not isinstance(pts, list) or len(pts) < 3:
            raise ValueError('polygon geometry must have at least 3 points.')
        for p in pts:
            if not (isinstance(p, (list, tuple)) and len(p) == 2):
                raise ValueError('polygon points must be [x, y] pairs.')
            if not all(isinstance(c, (int, float)) for c in p):
                raise ValueError('polygon point coordinates must be numeric.')

    elif region_type == RegionType.mask:
        fmt = geometry.get('format')
        size = geometry.get('size')
        if fmt not in {'rle', 'png_b64'}:
            raise ValueError('mask geometry format must be "rle" or "png_b64".')
        if not (isinstance(size, list) and len(size) == 2 and all(isinstance(s, int) for s in size)):
            raise ValueError('mask geometry "size" must be [height, width] ints.')
        if fmt == 'rle' and not isinstance(geometry.get('counts'), (list, str)):
            raise ValueError('mask rle requires "counts" (list of ints or string).')
        if fmt == 'png_b64' and not isinstance(geometry.get('data'), str):
            raise ValueError('mask png_b64 requires "data" (base64 string).')


class RegionAttrs(BaseModel):
    lesion_label: DiagnosisLabel | None = None
    lesion_location_clock: int | None = Field(default=None, ge=1, le=12)
    lesion_quadrant: LesionQuadrant | None = None
    lesion_size_percent: int | None = Field(default=None, ge=0, le=100)
    lesion_margins: LesionMargins | None = None
    punctation_present: bool | None = None
    punctation_severity: int | None = Field(default=None, ge=1, le=3)
    mosaic_present: bool | None = None
    mosaic_severity: int | None = Field(default=None, ge=1, le=3)
    region_notes: str | None = Field(default=None, max_length=4000)


class RegionCreate(RegionAttrs):
    region_type: RegionType
    geometry: dict[str, Any]

    @model_validator(mode='after')
    def _check_geometry(self):
        _validate_geometry(self.region_type, self.geometry)
        return self


class RegionPatch(RegionAttrs):
    """All fields optional. Geometry can be replaced; type is immutable."""
    geometry: dict[str, Any] | None = None

    def validate_geometry_for(self, region_type: RegionType) -> None:
        if self.geometry is not None:
            _validate_geometry(region_type, self.geometry)
