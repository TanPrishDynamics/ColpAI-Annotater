"""Render and persist an annotation's crop region as a standalone image.

The annotator draws a crop rectangle (stored on ImageAnnotation.crop_box in image
pixel coordinates). On submit we cut that rectangle out of the original image and
store it via the configured storage backend (local disk or Supabase), so the crop
travels alongside the original, the drawn regions, and the annotation metadata.
"""
from __future__ import annotations

import io
import os
from pathlib import PurePosixPath

from PIL import Image as PILImage

from app.services import storage
from app.services.exporters import overlay


def render_crop_bytes(annotation) -> bytes | None:
    """Return PNG bytes of the annotation's crop, or None if it can't be made.

    Returns None when there's no crop box, the box is degenerate, or the source
    image is unreadable -- callers treat a missing crop as non-fatal.
    """
    box = annotation.crop_box
    if not box:
        return None
    try:
        x, y, w, h = int(box['x']), int(box['y']), int(box['w']), int(box['h'])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None

    image_row = annotation.image
    if image_row is None:
        return None

    try:
        with storage.open_image(image_row.source_path) as fh:
            with PILImage.open(fh) as im:
                im.load()
                # Clamp to the image bounds so an oversized box can't error out.
                right = min(x + w, im.width)
                bottom = min(y + h, im.height)
                left = max(0, min(x, im.width))
                top = max(0, min(y, im.height))
                if right <= left or bottom <= top:
                    return None
                cropped = im.crop((left, top, right, bottom))
                if cropped.mode not in ('RGB', 'L'):
                    cropped = cropped.convert('RGB')
                out = io.BytesIO()
                cropped.save(out, format='PNG')
                return out.getvalue()
    except (FileNotFoundError, OSError, storage.StorageError):
        return None


def render_and_store_crop(annotation) -> str | None:
    """Render the crop and save it to storage; return its source ref or None.

    Storage failures are swallowed (return None) so a transient upload problem
    can't fail an otherwise-valid submit -- the crop can be regenerated later.
    """
    data = render_crop_bytes(annotation)
    if data is None:
        return None
    try:
        return storage.save_image(data, f"crops/{annotation.id}.png", content_type='image/png')
    except storage.StorageError:
        return None


def _box_xywh(box) -> tuple[int, int, int, int] | None:
    try:
        x, y, w, h = int(box['x']), int(box['y']), int(box['w']), int(box['h'])
    except (KeyError, TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def annotated_object_key(annotation) -> str:
    """Bucket key for the final annotated image: ``annotated/<patient>/<image>.png``.

    Grouped per patient and named after the original image file so it's easy to
    match back to the source. Falls back to ``unassigned`` when the image has no
    patient code.
    """
    image_row = annotation.image
    patient = (image_row.patient_code if image_row else None) or 'unassigned'
    name = PurePosixPath((image_row.source_path if image_row else '').replace('\\', '/')).name
    stem = os.path.splitext(name)[0] or annotation.id
    return f"annotated/{patient}/{stem}.png"


def render_annotated_bytes(annotation) -> bytes | None:
    """Return PNG bytes of the *final annotated image*: the drawn regions composited
    onto the original and then cut to the crop box (if one was drawn).

    Returns None when the source image is unreadable. With no crop box, the whole
    annotated image is returned; with no regions, it's just the crop.
    """
    image_row = annotation.image
    if image_row is None:
        return None
    composed = overlay.render_overlay(image_row, annotation)
    if composed is None:
        return None

    img = composed.convert('RGB')
    box = annotation.crop_box and _box_xywh(annotation.crop_box)
    if box:
        x, y, w, h = box
        left = max(0, min(x, img.width))
        top = max(0, min(y, img.height))
        right = min(x + w, img.width)
        bottom = min(y + h, img.height)
        if right > left and bottom > top:
            img = img.crop((left, top, right, bottom))

    out = io.BytesIO()
    img.save(out, format='PNG')
    return out.getvalue()


def render_and_store_annotated(annotation) -> str | None:
    """Render the final annotated image and store it under ``annotated/<patient>/``.

    Returns its storage reference, or None if it couldn't be rendered/stored
    (non-fatal: submit still succeeds and it can be regenerated later).
    """
    data = render_annotated_bytes(annotation)
    if data is None:
        return None
    try:
        return storage.save_image(data, annotated_object_key(annotation), content_type='image/png')
    except storage.StorageError:
        return None
