"""COCO detection/segmentation exporter.

Emits a single COCO JSON object:

- ``images``      one entry per exported image.
- ``annotations`` one entry per region. bbox is always present;
  ``segmentation`` is a polygon list for polygon regions, a COCO uncompressed
  RLE dict for mask regions, and a 4-point box polygon for bbox regions.
- ``categories``  the fixed diagnosis label set (stable ids across exports).

A region's category is its ``lesion_label``; if unset it falls back to the
image-level ``colposcopic_impression``. Regions with neither are skipped.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.services.exporters import geometry as geo
from app.services.exporters.selection import ExportSelection


def build_coco(selection: ExportSelection) -> dict:
    categories = [
        {'id': idx + 1, 'name': label.value, 'supercategory': 'lesion'}
        for idx, label in enumerate(selection.categories)
    ]
    cat_id = {label.value: idx + 1 for idx, label in enumerate(selection.categories)}

    images: list[dict] = []
    annotations: list[dict] = []
    image_num = {}
    next_image_id = 1
    next_ann_id = 1

    for image, ann in selection.pairs:
        coco_image_id = next_image_id
        next_image_id += 1
        image_num[image.id] = coco_image_id

        images.append({
            'id': coco_image_id,
            'file_name': image.source_path,
            'width': image.width_px,
            'height': image.height_px,
            'sha256': image.sha256,
            'dataset_source': image.dataset_source,
            'colposcopic_impression': (
                ann.colposcopic_impression.value if ann.colposcopic_impression else None
            ),
        })

        for region in ann.regions:
            label = region.lesion_label or ann.colposcopic_impression
            if label is None:
                continue  # nothing to categorise this region as
            category_id = cat_id.get(label.value)
            if category_id is None:
                continue

            bbox = geo.region_bbox(region)
            if bbox is None:
                continue

            segmentation, area = _segmentation_for(region, bbox)

            annotations.append({
                'id': next_ann_id,
                'image_id': coco_image_id,
                'category_id': category_id,
                'bbox': [round(v, 2) for v in bbox],
                'area': round(area, 2),
                'iscrowd': 0,
                'segmentation': segmentation,
                'region_id': region.id,
            })
            next_ann_id += 1

    return {
        'info': {
            'description': 'ColpAI colposcopy annotations',
            'dataset_source': selection.dataset_source or 'all',
            'status_filter': selection.status_filter,
            'date_created': datetime.now(timezone.utc).isoformat(),
        },
        'licenses': [],
        'images': images,
        'annotations': annotations,
        'categories': categories,
    }


def _segmentation_for(region, bbox):
    """Return ``(segmentation, area)`` appropriate to the region type."""
    from app.models.enums import RegionType

    if region.region_type == RegionType.polygon:
        flat = geo.polygon_flat(region)
        if flat:
            return [flat], geo.polygon_area(flat)

    if region.region_type == RegionType.mask:
        mask = geo.decode_mask(region.geometry or {})
        if mask is not None and mask.any():
            return geo.encode_rle(mask), float(int(mask.sum()))

    # bbox (or degenerate fallback): box as a 4-point polygon.
    x, y, w, h = bbox
    poly = [x, y, x + w, y, x + w, y + h, x, y + h]
    return [poly], float(w * h)
