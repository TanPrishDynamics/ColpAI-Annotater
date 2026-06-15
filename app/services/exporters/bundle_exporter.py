"""Full bundle export: the original images, annotation overlays, and label files.

Produces one zip an admin can hand off whole::

    images/<image_id>.<ext>      original image files (as ingested)
    overlays/<image_id>.png      same images with annotations drawn on
    labels/annotations_image.csv per-image CSV
    labels/annotations_region.csv per-region CSV
    labels/coco.json             COCO detection/segmentation JSON
    manifest.csv                 image_id -> source path, label, file status
    README.txt                   what's inside

Missing source files are skipped gracefully and flagged in manifest.csv.
"""
from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from datetime import datetime, timezone

from app.services import crop, storage
from app.services.exporters import coco_exporter, csv_exporter, overlay
from app.services.exporters.selection import ExportSelection


def build_bundle_zip(selection: ExportSelection) -> bytes:
    buf = io.BytesIO()
    manifest = [('image_id', 'dataset_source', 'source_path',
                 'impression', 'region_count', 'original_included', 'overlay_included',
                 'crop_included')]

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for image, ann in selection.pairs:
            ext = os.path.splitext(image.source_path)[1].lower() or '.jpg'
            original_ok = _add_original(zf, image, ext)
            overlay_ok = _add_overlay(zf, image, ann)
            crop_ok = _add_crop(zf, image, ann)
            manifest.append((
                image.id,
                image.dataset_source,
                image.source_path,
                ann.colposcopic_impression.value if ann.colposcopic_impression else '',
                len(ann.regions),
                'yes' if original_ok else 'MISSING',
                'yes' if overlay_ok else 'no',
                'yes' if crop_ok else 'no',
            ))

        # Label files alongside the pictures.
        zf.writestr('labels/annotations_image.csv', csv_exporter.export_image_csv(selection))
        zf.writestr('labels/annotations_region.csv', csv_exporter.export_region_csv(selection))
        zf.writestr('labels/coco.json', json.dumps(coco_exporter.build_coco(selection), indent=2))

        man_buf = io.StringIO()
        csv.writer(man_buf).writerows(manifest)
        zf.writestr('manifest.csv', man_buf.getvalue())
        zf.writestr('README.txt', _readme(selection, len(manifest) - 1))

    return buf.getvalue()


def _add_original(zf, image, ext) -> bool:
    try:
        with storage.open_image(image.source_path) as f:
            zf.writestr(f"images/{image.id}{ext}", f.read())
        return True
    except (FileNotFoundError, OSError, storage.StorageError):
        return False


def _add_overlay(zf, image, ann) -> bool:
    img = overlay.render_overlay(image, ann)
    if img is None:
        return False
    out = io.BytesIO()
    img.convert('RGB').save(out, format='PNG')
    zf.writestr(f"overlays/{image.id}.png", out.getvalue())
    return True


def _add_crop(zf, image, ann) -> bool:
    data = crop.render_crop_bytes(ann)
    if data is None:
        return False
    zf.writestr(f"crops/{image.id}.png", data)
    return True


def _readme(selection: ExportSelection, n: int) -> str:
    return (
        "ColpAI annotation bundle\n"
        "========================\n\n"
        f"Generated:   {datetime.now(timezone.utc).isoformat()}\n"
        f"Dataset:     {selection.dataset_source or 'all'}\n"
        f"Status:      {selection.status_filter}\n"
        f"Images:      {n}\n\n"
        "Contents:\n"
        "  images/      original image files, named by image id\n"
        "  overlays/    the same images with annotations drawn on (color = diagnosis)\n"
        "  crops/       the annotator's crop region, where one was drawn\n"
        "  labels/      annotations_image.csv, annotations_region.csv, coco.json\n"
        "  manifest.csv image id -> source path, label, and whether the file was found\n\n"
        "Any image whose source file was unavailable on the server is listed as\n"
        "MISSING in manifest.csv and omitted from images/ and overlays/.\n"
    )
