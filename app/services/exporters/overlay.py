"""Render annotations onto the original image.

Produces a human-readable picture: bounding boxes and polygon outlines drawn in
the diagnosis colour, mask regions as a translucent fill, each tagged with its
label. Used by the bundle export so an admin/clinician can eyeball what was
annotated without loading the data into a model.
"""
from __future__ import annotations

import numpy as np
from PIL import Image as PILImage, ImageDraw, ImageFont

from app.models.enums import RegionType
from app.services import storage
from app.services.exporters import geometry as geo

# Same palette as the dashboard + mask export, as RGB.
_PALETTE = {
    'NORMAL': (46, 154, 90),
    'CIN1': (201, 165, 49),
    'CIN2': (207, 122, 49),
    'CIN3': (200, 74, 58),
    'AIS': (138, 79, 191),
    'INVASIVE_CANCER': (122, 31, 31),
}
_DEFAULT = (122, 163, 255)


def _color_for(label) -> tuple[int, int, int]:
    if label is None:
        return _DEFAULT
    return _PALETTE.get(label.value, _DEFAULT)


def render_overlay(image_row, annotation) -> PILImage.Image | None:
    """Open ``image_row.source_path`` and draw ``annotation``'s regions on it.

    Returns an RGBA PIL image, or None if the source file can't be opened.
    """
    try:
        with storage.open_image(image_row.source_path) as fh:
            base = PILImage.open(fh).convert('RGBA')
    except (FileNotFoundError, OSError, storage.StorageError):
        return None

    w, h = base.size
    overlay = PILImage.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font()

    for region in annotation.regions:
        label = region.lesion_label or annotation.colposcopic_impression
        color = _color_for(label)
        name = label.value if label else 'unlabeled'
        _draw_region(draw, overlay, region, color, name, font, w, h)

    return PILImage.alpha_composite(base, overlay)


def _draw_region(draw, overlay, region, color, name, font, w, h):
    line = color + (255,)
    fill = color + (70,)

    if region.region_type == RegionType.bbox:
        g = region.geometry
        x, y = g['x'], g['y']
        draw.rectangle([x, y, x + g['w'], y + g['h']], outline=line, width=3)
        _label(draw, x, y, name, color, font)

    elif region.region_type == RegionType.polygon:
        pts = [(p[0], p[1]) for p in region.geometry.get('points', [])]
        if len(pts) >= 3:
            draw.polygon(pts, outline=line, fill=fill)
            x0 = min(p[0] for p in pts)
            y0 = min(p[1] for p in pts)
            _label(draw, x0, y0, name, color, font)

    elif region.region_type == RegionType.mask:
        mask = geo.rasterize_region(region, h, w)
        if mask is not None and mask.any():
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            rgba[mask] = (*color, 110)
            tint = PILImage.fromarray(rgba, mode='RGBA')
            overlay.alpha_composite(tint)
            ys, xs = np.where(mask)
            _label(draw, int(xs.min()), int(ys.min()), name, color, font)


def _label(draw, x, y, text, color, font):
    pad = 2
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = len(text) * 6, 11
    ty = max(0, y - th - 2 * pad)
    draw.rectangle([x, ty, x + tw + 2 * pad, ty + th + 2 * pad], fill=color + (220,))
    draw.text((x + pad, ty + pad), text, fill=(255, 255, 255, 255), font=font)


def _load_font():
    try:
        return ImageFont.truetype('DejaVuSans.ttf', 14)
    except Exception:
        return ImageFont.load_default()
