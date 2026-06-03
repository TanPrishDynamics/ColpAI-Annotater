"""Dataset exporters (Phase 5).

Each exporter turns the annotation DB into a training-ready artifact:

- ``csv_exporter``  - flat per-annotation (and optional per-region) CSV.
- ``coco_exporter`` - COCO detection/segmentation JSON.
- ``yolo_exporter`` - YOLO ``.txt`` labels + ``data.yaml`` (zipped).
- ``mask_exporter`` - rasterised semantic-segmentation PNGs (zipped).

They all share one notion of "what to export", defined in ``selection`` so the
four formats stay consistent: the same set of images and the same chosen
annotation per image feed every exporter.
"""
from app.services.exporters.selection import (
    ExportSelection,
    gather_export_selection,
)

__all__ = ['ExportSelection', 'gather_export_selection']
