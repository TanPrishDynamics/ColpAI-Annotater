"""Segmentation-mask exporter.

For every exported image with regions, rasterises a semantic mask and writes a
zip containing:

- ``masks/<image_id>.png``   single-channel PNG, pixel value = class id
  (0 = background, 1..N = categories in shared order). The training-ready file.
- ``preview/<image_id>.png``  RGB colour-mapped version for eyeballing.
- ``classes.csv``            ``pixel_value,class_name,r,g,b`` legend.

On overlap, the more severe label wins (regions are painted in ascending
severity so the worst grade ends up on top).
"""
from __future__ import annotations

import csv
import io
import zipfile

import numpy as np
from PIL import Image as PILImage

from app.services.exporters import geometry as geo
from app.services.exporters.selection import ExportSelection

# Palette aligned with the dashboard colour-coding (PLAN.md section 5).
_PALETTE = {
    'NORMAL': (0, 160, 0),
    'CIN1': (220, 220, 0),
    'CIN2': (255, 140, 0),
    'CIN3': (220, 0, 0),
    'AIS': (150, 0, 200),
    'INVASIVE_CANCER': (110, 0, 0),
    'INFLAMMATION': (230, 100, 190),
    'INFECTION': (0, 180, 180),
    'EROSION': (190, 110, 30),
}


def build_mask_zip(selection: ExportSelection) -> bytes:
    class_pixel = {label.value: idx + 1 for idx, label in enumerate(selection.categories)}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for image, ann in selection.pairs:
            w, h = image.width_px, image.height_px
            if not w or not h or not ann.regions:
                continue

            semantic = np.zeros((h, w), dtype=np.uint8)

            def _get_label(r):
                if r.lesion_label:
                    return r.lesion_label.value
                elif ann.colposcopic_impression:
                    return ann.colposcopic_impression[0]
                return ''

            # Paint ascending severity so the worst grade wins on overlap.
            painted = sorted(
                ann.regions,
                key=lambda r: class_pixel.get(_get_label(r), 0),
            )
            for region in painted:
                label_val = _get_label(region)
                if not label_val or label_val not in class_pixel:
                    continue
                mask = geo.rasterize_region(region, h, w)
                if mask is None:
                    continue
                semantic[mask] = class_pixel[label_val]

            zf.writestr(f"masks/{image.id}.png", _png_bytes(PILImage.fromarray(semantic, mode='L')))
            zf.writestr(f"preview/{image.id}.png", _png_bytes(_colorize(semantic, selection)))

        zf.writestr('classes.csv', _classes_csv(selection))

    return buf.getvalue()


def _colorize(semantic: np.ndarray, selection: ExportSelection) -> PILImage.Image:
    h, w = semantic.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    for idx, label in enumerate(selection.categories):
        color = _PALETTE.get(label.value, (255, 255, 255))
        rgb[semantic == idx + 1] = color
    return PILImage.fromarray(rgb, mode='RGB')


def _classes_csv(selection: ExportSelection) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['pixel_value', 'class_name', 'r', 'g', 'b'])
    writer.writerow([0, 'background', 0, 0, 0])
    for idx, label in enumerate(selection.categories):
        r, g, b = _PALETTE.get(label.value, (255, 255, 255))
        writer.writerow([idx + 1, label.value, r, g, b])
    return out.getvalue()


def _png_bytes(im: PILImage.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, format='PNG')
    return b.getvalue()
