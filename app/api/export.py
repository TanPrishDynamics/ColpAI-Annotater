"""Dataset export API (Phase 5). Restricted to reviewer / admin.

- GET /api/v1/export/summary           - counts for the current selection (UI preview)
- GET /api/v1/export/csv?level=image   - flat CSV (image or region level)
- GET /api/v1/export/coco              - COCO detection/segmentation JSON
- GET /api/v1/export/yolo              - YOLO labels + data.yaml (zip)
- GET /api/v1/export/masks             - semantic-mask PNGs (zip)

All accept ``?dataset=<source>&status=reviewed|submitted|all``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user, login_required

from app.api.errors import error_response
from app.models.enums import UserRole
from app.schemas.export import ExportQuery
from app.services.exporters import gather_export_selection
from app.services.exporters import (
    bundle_exporter,
    coco_exporter,
    csv_exporter,
    mask_exporter,
    yolo_exporter,
)

bp = Blueprint('export', __name__, url_prefix='/api/v1/export')

EXPORTER_ROLES = {UserRole.reviewer.value, UserRole.admin.value}


def _require_exporter():
    if current_user.role.value not in EXPORTER_ROLES:
        return error_response('forbidden', 'Reviewer or admin role required to export.', status=403)
    return None


def _query() -> ExportQuery:
    return ExportQuery.model_validate(request.args.to_dict())


def _stamp(suffix: str, q: ExportQuery) -> str:
    ts = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    scope = q.dataset or 'all'
    return f"colpai_{scope}_{q.status}_{ts}.{suffix}"


def _download(body, mimetype: str, filename: str) -> Response:
    resp = Response(body, mimetype=mimetype)
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


@bp.get('/summary')
@login_required
def summary():
    guard = _require_exporter()
    if guard is not None:
        return guard
    q = _query()
    sel = gather_export_selection(dataset_source=q.dataset, status=q.status)
    region_count = sum(len(ann.regions) for _, ann in sel.pairs)
    return jsonify({
        'dataset': q.dataset or 'all',
        'status': q.status,
        'image_count': len(sel),
        'region_count': region_count,
        'categories': [c.value for c in sel.categories],
    })


@bp.get('/csv')
@login_required
def export_csv():
    guard = _require_exporter()
    if guard is not None:
        return guard
    q = _query()
    sel = gather_export_selection(dataset_source=q.dataset, status=q.status)
    if q.level == 'region':
        body = csv_exporter.export_region_csv(sel)
    else:
        body = csv_exporter.export_image_csv(sel)
    return _download(body, 'text/csv', _stamp(f'{q.level}.csv', q))


@bp.get('/coco')
@login_required
def export_coco():
    guard = _require_exporter()
    if guard is not None:
        return guard
    q = _query()
    sel = gather_export_selection(dataset_source=q.dataset, status=q.status)
    body = json.dumps(coco_exporter.build_coco(sel), indent=2)
    return _download(body, 'application/json', _stamp('coco.json', q))


@bp.get('/yolo')
@login_required
def export_yolo():
    guard = _require_exporter()
    if guard is not None:
        return guard
    q = _query()
    sel = gather_export_selection(dataset_source=q.dataset, status=q.status)
    body = yolo_exporter.build_yolo_zip(sel)
    return _download(body, 'application/zip', _stamp('yolo.zip', q))


@bp.get('/masks')
@login_required
def export_masks():
    guard = _require_exporter()
    if guard is not None:
        return guard
    q = _query()
    sel = gather_export_selection(dataset_source=q.dataset, status=q.status)
    body = mask_exporter.build_mask_zip(sel)
    return _download(body, 'application/zip', _stamp('masks.zip', q))


@bp.get('/bundle')
@login_required
def export_bundle():
    """Originals + annotation overlays + label files, all in one zip."""
    guard = _require_exporter()
    if guard is not None:
        return guard
    q = _query()
    sel = gather_export_selection(dataset_source=q.dataset, status=q.status)
    body = bundle_exporter.build_bundle_zip(sel)
    return _download(body, 'application/zip', _stamp('bundle.zip', q))
