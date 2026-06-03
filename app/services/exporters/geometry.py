"""Geometry helpers shared by the COCO / YOLO / mask exporters.

Region geometry is stored as JSON in three shapes (see ``schemas/region.py``):

- bbox     ``{"x", "y", "w", "h"}``
- polygon  ``{"points": [[x, y], ...]}``
- mask     ``{"format": "rle"|"png_b64", "size": [h, w], ...}``

These helpers normalise all three into the things exporters need: an
``[x, y, w, h]`` box, a flat polygon list, a rasterised boolean array, and a
COCO-style uncompressed RLE. RLE encode/decode are implemented with numpy so we
don't pull in the native ``pycocotools`` build; the ``counts`` list we emit is
the COCO *uncompressed* RLE format and round-trips through pycocotools.
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image as PILImage, ImageDraw

from app.models.enums import RegionType


def region_bbox(region) -> list[float] | None:
    """Return ``[x, y, w, h]`` in pixels for any region type, or None."""
    g = region.geometry or {}
    if region.region_type == RegionType.bbox:
        return [float(g['x']), float(g['y']), float(g['w']), float(g['h'])]
    if region.region_type == RegionType.polygon:
        pts = g.get('points') or []
        if not pts:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0 = min(xs), min(ys)
        return [float(x0), float(y0), float(max(xs) - x0), float(max(ys) - y0)]
    if region.region_type == RegionType.mask:
        mask = decode_mask(g)
        if mask is None or not mask.any():
            return None
        return bbox_from_mask(mask)
    return None


def polygon_flat(region) -> list[float] | None:
    """COCO polygon segmentation: ``[x1, y1, x2, y2, ...]`` or None."""
    if region.region_type != RegionType.polygon:
        return None
    pts = (region.geometry or {}).get('points') or []
    if len(pts) < 3:
        return None
    flat: list[float] = []
    for x, y in pts:
        flat.extend([float(x), float(y)])
    return flat


def polygon_area(points_flat: list[float]) -> float:
    """Shoelace area of a flat ``[x1, y1, ...]`` polygon."""
    xs = points_flat[0::2]
    ys = points_flat[1::2]
    n = len(xs)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += xs[i] * ys[j] - xs[j] * ys[i]
    return abs(area) / 2.0


def bbox_from_mask(mask: np.ndarray) -> list[float]:
    ys, xs = np.where(mask)
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return [float(x0), float(y0), float(x1 - x0 + 1), float(y1 - y0 + 1)]


def decode_mask(geometry: dict) -> np.ndarray | None:
    """Decode a stored mask geometry to a boolean ``(h, w)`` array."""
    fmt = geometry.get('format')
    size = geometry.get('size')
    if not (isinstance(size, (list, tuple)) and len(size) == 2):
        return None
    h, w = int(size[0]), int(size[1])

    if fmt == 'rle':
        return decode_rle(geometry.get('counts'), (h, w))
    if fmt == 'png_b64':
        data = geometry.get('data')
        if not isinstance(data, str):
            return None
        raw = base64.b64decode(_strip_data_url(data))
        with PILImage.open(io.BytesIO(raw)) as im:
            arr = np.array(im.convert('L'))
        return arr > 127
    return None


def encode_rle(mask: np.ndarray) -> dict:
    """Boolean ``(h, w)`` array -> COCO uncompressed RLE ``{size, counts}``."""
    h, w = mask.shape
    flat = np.asarray(mask, dtype=np.uint8).flatten(order='F')  # column-major
    if flat.size == 0:
        return {'size': [h, w], 'counts': [0]}
    change = np.where(np.diff(flat) != 0)[0] + 1
    boundaries = np.concatenate(([0], change, [flat.size]))
    runs = np.diff(boundaries).tolist()
    # COCO counts always start with a run of background (0). If the first pixel
    # is foreground, prepend a zero-length background run.
    if flat[0] == 1:
        runs = [0] + runs
    return {'size': [int(h), int(w)], 'counts': [int(c) for c in runs]}


def decode_rle(counts, size) -> np.ndarray | None:
    if counts is None:
        return None
    h, w = int(size[0]), int(size[1])
    flat = np.zeros(h * w, dtype=np.uint8)
    idx = 0
    value = 0
    for c in counts:
        c = int(c)
        if value == 1:
            flat[idx:idx + c] = 1
        idx += c
        value ^= 1
    return flat.reshape((h, w), order='F').astype(bool)


def rasterize_region(region, h: int, w: int) -> np.ndarray | None:
    """Rasterise any region into a boolean ``(h, w)`` mask."""
    g = region.geometry or {}
    if region.region_type == RegionType.mask:
        mask = decode_mask(g)
        if mask is None:
            return None
        if mask.shape != (h, w):  # defensive: resize to the image grid
            with PILImage.fromarray(mask.astype(np.uint8) * 255) as im:
                im = im.resize((w, h), PILImage.NEAREST)
                mask = np.array(im) > 127
        return mask

    canvas = PILImage.new('L', (w, h), 0)
    draw = ImageDraw.Draw(canvas)
    if region.region_type == RegionType.bbox:
        x, y, bw, bh = g['x'], g['y'], g['w'], g['h']
        draw.rectangle([x, y, x + bw, y + bh], fill=1)
    elif region.region_type == RegionType.polygon:
        pts = [(p[0], p[1]) for p in g.get('points', [])]
        if len(pts) < 3:
            return None
        draw.polygon(pts, fill=1)
    else:
        return None
    return np.array(canvas, dtype=bool)


def _strip_data_url(data: str) -> str:
    """Tolerate ``data:image/png;base64,xxxx`` as well as a bare base64 string."""
    if data.startswith('data:') and ',' in data:
        return data.split(',', 1)[1]
    return data
