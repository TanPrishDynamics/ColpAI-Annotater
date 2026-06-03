"""Region (Layer C) API.

- POST   /api/v1/annotations/{annotation_id}/regions
- PATCH  /api/v1/regions/{region_id}
- DELETE /api/v1/regions/{region_id}

Regions can only be edited while their parent annotation is a draft owned by
the current user. Reviewers/admins can read but not edit through this endpoint.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.api.errors import error_response
from app.extensions import db
from app.models import Image, ImageAnnotation, Region
from app.models.enums import AnnotationStatus, RegionType
from app.schemas.region import RegionCreate, RegionPatch

annotation_regions_bp = Blueprint(
    'annotation_regions', __name__, url_prefix='/api/v1/annotations'
)
regions_bp = Blueprint('regions', __name__, url_prefix='/api/v1/regions')


def _check_geometry_bounds(region_type: RegionType, geometry: dict, image: Image) -> str | None:
    """Return an error message if the geometry strays outside the image, else None."""
    w, h = image.width_px, image.height_px
    if not w or not h:
        return None  # ingestion didn't capture dimensions; skip the bounds check.

    if region_type == RegionType.bbox:
        if geometry['x'] < 0 or geometry['y'] < 0:
            return 'bbox origin must be within the image.'
        if geometry['x'] + geometry['w'] > w or geometry['y'] + geometry['h'] > h:
            return f'bbox extends past image bounds ({w}x{h}).'
    elif region_type == RegionType.polygon:
        for x, y in geometry['points']:
            if x < 0 or y < 0 or x > w or y > h:
                return f'polygon point ({x}, {y}) is outside image bounds ({w}x{h}).'
    elif region_type == RegionType.mask:
        size = geometry.get('size')
        if size and (size[0] != h or size[1] != w):
            return f'mask size {size} does not match image ({h}x{w}).'
    return None


def _load_editable_annotation(annotation_id: str) -> tuple[ImageAnnotation | None, str | None, int]:
    """Load the annotation if the current user can edit it. Returns (ann, error_message, status_code)."""
    ann = db.session.get(ImageAnnotation, annotation_id)
    if ann is None:
        return None, 'Annotation not found.', 404
    if ann.annotator_id != current_user.id:
        return None, 'You can only modify regions on your own annotations.', 403
    if ann.status != AnnotationStatus.draft:
        return None, f'Annotation is {ann.status.value}; regions are read-only.', 409
    return ann, None, 200


def _apply_attrs(region: Region, payload) -> None:
    for field in ('lesion_label', 'lesion_location_clock', 'lesion_quadrant',
                  'lesion_size_percent', 'lesion_margins', 'punctation_present',
                  'punctation_severity', 'mosaic_present', 'mosaic_severity',
                  'region_notes'):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(region, field, value)


@annotation_regions_bp.post('/<annotation_id>/regions')
@login_required
def create_region(annotation_id: str):
    ann, err, status = _load_editable_annotation(annotation_id)
    if ann is None:
        code = 'not_found' if status == 404 else 'forbidden' if status == 403 else 'not_editable'
        return error_response(code, err, status=status)

    payload = RegionCreate.model_validate(request.get_json(silent=True) or {})

    bounds_err = _check_geometry_bounds(payload.region_type, payload.geometry, ann.image)
    if bounds_err:
        return error_response('geometry_out_of_bounds', bounds_err, status=422)

    region = Region(
        image_annotation_id=ann.id,
        region_type=payload.region_type,
        geometry=payload.geometry,
    )
    _apply_attrs(region, payload)
    db.session.add(region)
    db.session.commit()
    return jsonify(region.to_dict()), 201


@annotation_regions_bp.get('/<annotation_id>/regions')
@login_required
def list_regions(annotation_id: str):
    ann = db.session.get(ImageAnnotation, annotation_id)
    if ann is None:
        return error_response('not_found', 'Annotation not found.', status=404)
    if ann.annotator_id != current_user.id and current_user.role.value not in {'reviewer', 'admin'}:
        return error_response('forbidden', 'Not your annotation.', status=403)
    return jsonify({'items': [r.to_dict() for r in ann.regions]})


def _load_editable_region(region_id: str) -> tuple[Region | None, str | None, int]:
    region = db.session.get(Region, region_id)
    if region is None:
        return None, 'Region not found.', 404
    ann = region.annotation
    if ann.annotator_id != current_user.id:
        return None, 'You can only modify your own regions.', 403
    if ann.status != AnnotationStatus.draft:
        return None, f'Parent annotation is {ann.status.value}; regions are read-only.', 409
    return region, None, 200


@regions_bp.patch('/<region_id>')
@login_required
def update_region(region_id: str):
    region, err, status = _load_editable_region(region_id)
    if region is None:
        code = 'not_found' if status == 404 else 'forbidden' if status == 403 else 'not_editable'
        return error_response(code, err, status=status)

    payload = RegionPatch.model_validate(request.get_json(silent=True) or {})
    payload.validate_geometry_for(region.region_type)

    if payload.geometry is not None:
        bounds_err = _check_geometry_bounds(region.region_type, payload.geometry, region.annotation.image)
        if bounds_err:
            return error_response('geometry_out_of_bounds', bounds_err, status=422)
        region.geometry = payload.geometry

    _apply_attrs(region, payload)
    db.session.commit()
    return jsonify(region.to_dict())


@regions_bp.delete('/<region_id>')
@login_required
def delete_region(region_id: str):
    region, err, status = _load_editable_region(region_id)
    if region is None:
        code = 'not_found' if status == 404 else 'forbidden' if status == 403 else 'not_editable'
        return error_response(code, err, status=status)

    db.session.delete(region)
    db.session.commit()
    return '', 204
