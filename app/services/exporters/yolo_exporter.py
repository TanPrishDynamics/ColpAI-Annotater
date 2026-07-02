"""YOLO detection exporter.

Produces a zip with:

- ``labels/<image_id>.txt`` - one line per region: ``cls cx cy w h`` (all
  normalised 0-1, YOLO convention). Polygon/mask regions are reduced to their
  bounding box.
- ``classes.txt``           - class names, one per line (index = class id).
- ``data.yaml``             - Ultralytics-style dataset descriptor.
- ``image_index.csv``       - maps each label file back to its source image path.

Class id = index in the shared category order, so it matches the COCO export
minus one (COCO categories are 1-based, YOLO is 0-based).
"""
from __future__ import annotations

import csv
import io
import zipfile

from app.services.exporters import geometry as geo
from app.services.exporters.selection import ExportSelection


def build_yolo_zip(selection: ExportSelection) -> bytes:
    class_names = [label.value for label in selection.categories]
    class_id = {label.value: idx for idx, label in enumerate(selection.categories)}

    buf = io.BytesIO()
    index_rows = [('label_file', 'image_id', 'source_path', 'width_px', 'height_px')]

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for image, ann in selection.pairs:
            w, h = image.width_px, image.height_px
            if not w or not h:
                continue  # cannot normalise without dimensions

            lines: list[str] = []
            for region in ann.regions:
                if region.lesion_label:
                    label_val = region.lesion_label.value
                elif ann.colposcopic_impression:
                    label_val = ann.colposcopic_impression[0]
                else:
                    label_val = None

                if not label_val or label_val not in class_id:
                    continue
                bbox = geo.region_bbox(region)
                if bbox is None:
                    continue
                x, y, bw, bh = bbox
                cx = (x + bw / 2) / w
                cy = (y + bh / 2) / h
                nw = bw / w
                nh = bh / h
                # clamp to [0, 1] in case a region grazed the border
                cx, cy = _clamp01(cx), _clamp01(cy)
                nw, nh = _clamp01(nw), _clamp01(nh)
                lines.append(
                    f"{class_id[label_val]} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}"
                )

            label_file = f"labels/{image.id}.txt"
            zf.writestr(label_file, "\n".join(lines) + ("\n" if lines else ""))
            index_rows.append((label_file, image.id, image.source_path, w, h))

        zf.writestr('classes.txt', "\n".join(class_names) + "\n")
        zf.writestr('data.yaml', _data_yaml(class_names, selection))

        idx_buf = io.StringIO()
        csv.writer(idx_buf).writerows(index_rows)
        zf.writestr('image_index.csv', idx_buf.getvalue())

    return buf.getvalue()


def _data_yaml(class_names: list[str], selection: ExportSelection) -> str:
    names = "\n".join(f"  {i}: {name}" for i, name in enumerate(class_names))
    return (
        "# Ultralytics YOLO dataset config (ColpAI export)\n"
        f"# dataset_source: {selection.dataset_source or 'all'}\n"
        f"# status_filter: {selection.status_filter}\n"
        "path: .\n"
        "train: images\n"
        "val: images\n"
        f"nc: {len(class_names)}\n"
        "names:\n"
        f"{names}\n"
    )


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))
