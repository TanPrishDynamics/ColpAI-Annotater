"""End-to-end tests for the Phase 5 exporters.

Builds one image with three regions (bbox, polygon, mask) in an in-memory DB,
then exercises every exporter plus the RLE round-trip. Run:

    ./venv/bin/python -m pytest tests/test_exporters.py -q
"""
from __future__ import annotations

import base64
import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image as PILImage

from app import create_app
from app.extensions import db
from app.models import Image, ImageAnnotation, Region, User
from app.models.enums import (
    AnnotationStatus,
    DiagnosisLabel,
    RegionType,
    UserRole,
)
from app.services.exporters import gather_export_selection, geometry as geo
from app.services.exporters import (
    bundle_exporter,
    coco_exporter,
    csv_exporter,
    mask_exporter,
    yolo_exporter,
)

IMG_W, IMG_H = 200, 100


@pytest.fixture()
def app(tmp_path):
    # A real image file on disk so the bundle exporter can read originals + overlays.
    global _IMG_FILE
    _IMG_FILE = str(tmp_path / 'img_0001.jpg')
    PILImage.new('RGB', (IMG_W, IMG_H), (40, 40, 40)).save(_IMG_FILE)

    app = create_app('test')
    with app.app_context():
        db.create_all()
        _seed()
        yield app
        db.session.remove()
        db.drop_all()


_IMG_FILE = '/data/img_0001.jpg'


def _png_b64_mask() -> str:
    """A solid 20x20 block near the top-left, encoded as a full-image PNG mask."""
    arr = np.zeros((IMG_H, IMG_W), dtype=np.uint8)
    arr[10:30, 10:30] = 255
    bio = io.BytesIO()
    PILImage.fromarray(arr, mode='L').save(bio, format='PNG')
    return base64.b64encode(bio.getvalue()).decode()


def _seed():
    user = User(username='annotator1', role=UserRole.annotator)
    user.set_password('pw')
    db.session.add(user)

    reviewer = User(username='reviewer1', role=UserRole.reviewer)
    reviewer.set_password('pw')
    db.session.add(reviewer)
    db.session.flush()

    image = Image(
        sha256='a' * 64,
        source_path=_IMG_FILE,
        dataset_source='colpo_positive',
        width_px=IMG_W,
        height_px=IMG_H,
    )
    db.session.add(image)
    db.session.flush()

    ann = ImageAnnotation(
        image_id=image.id,
        annotator_id=user.id,
        status=AnnotationStatus.reviewed,
        version=1,
        colposcopic_impression=DiagnosisLabel.CIN2,
        confidence=4,
    )
    db.session.add(ann)
    db.session.flush()

    db.session.add_all([
        Region(
            image_annotation_id=ann.id,
            region_type=RegionType.bbox,
            geometry={'x': 50, 'y': 20, 'w': 40, 'h': 30},
            lesion_label=DiagnosisLabel.CIN1,
        ),
        Region(
            image_annotation_id=ann.id,
            region_type=RegionType.polygon,
            geometry={'points': [[100, 10], [150, 15], [140, 60], [95, 50]]},
            lesion_label=DiagnosisLabel.CIN3,
        ),
        Region(
            image_annotation_id=ann.id,
            region_type=RegionType.mask,
            geometry={'format': 'png_b64', 'size': [IMG_H, IMG_W], 'data': _png_b64_mask()},
            lesion_label=DiagnosisLabel.CIN2,
        ),
    ])
    db.session.commit()


def test_selection_picks_reviewed(app):
    sel = gather_export_selection(status='reviewed')
    assert len(sel) == 1
    image, ann = sel.pairs[0]
    assert ann.status == AnnotationStatus.reviewed
    assert len(ann.regions) == 3


def test_rle_roundtrip():
    mask = np.zeros((100, 200), dtype=bool)
    mask[10:30, 10:30] = True
    mask[40:55, 120:140] = True
    rle = geo.encode_rle(mask)
    back = geo.decode_rle(rle['counts'], rle['size'])
    assert np.array_equal(mask, back)
    assert int(rle['size'][0]) == 100 and int(rle['size'][1]) == 200


def test_csv_image_and_region(app):
    sel = gather_export_selection(status='reviewed')
    image_csv = csv_exporter.export_image_csv(sel)
    assert 'colposcopic_impression' in image_csv.splitlines()[0]
    assert 'CIN2' in image_csv  # the image-level impression
    assert image_csv.count('\n') == 2  # header + 1 row

    region_csv = csv_exporter.export_region_csv(sel)
    assert region_csv.count('\n') == 4  # header + 3 regions
    assert 'CIN1' in region_csv and 'CIN3' in region_csv


def test_coco_structure(app):
    sel = gather_export_selection(status='reviewed')
    coco = coco_exporter.build_coco(sel)
    assert len(coco['images']) == 1
    assert len(coco['annotations']) == 3
    from app.services.exporters.selection import CATEGORY_ORDER
    assert len(coco['categories']) == len(CATEGORY_ORDER)

    seg_types = {type(a['segmentation']).__name__ for a in coco['annotations']}
    # polygon/bbox -> list, mask -> dict (RLE)
    assert 'list' in seg_types and 'dict' in seg_types

    for a in coco['annotations']:
        x, y, w, h = a['bbox']
        assert w > 0 and h > 0
        assert 0 <= x <= IMG_W and 0 <= y <= IMG_H

    # round-trip the JSON to ensure it's serialisable
    json.loads(json.dumps(coco))


def test_yolo_zip(app):
    sel = gather_export_selection(status='reviewed')
    data = yolo_exporter.build_yolo_zip(sel)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert 'classes.txt' in names and 'data.yaml' in names
    label_files = [n for n in names if n.startswith('labels/')]
    assert len(label_files) == 1
    lines = zf.read(label_files[0]).decode().strip().splitlines()
    assert len(lines) == 3
    for line in lines:
        cls, cx, cy, w, h = line.split()
        assert 0 <= float(cx) <= 1 and 0 <= float(cy) <= 1
        assert 0 < float(w) <= 1 and 0 < float(h) <= 1


def test_mask_zip(app):
    sel = gather_export_selection(status='reviewed')
    data = mask_exporter.build_mask_zip(sel)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert 'classes.csv' in names
    mask_files = [n for n in names if n.startswith('masks/')]
    assert len(mask_files) == 1
    with PILImage.open(io.BytesIO(zf.read(mask_files[0]))) as im:
        arr = np.array(im)
    assert arr.shape == (IMG_H, IMG_W)
    present = set(np.unique(arr).tolist())
    # background + at least the painted classes (CIN1=2, CIN2=3, CIN3=4)
    assert 0 in present
    assert present & {2, 3, 4}


def test_bundle_zip(app):
    sel = gather_export_selection(status='reviewed')
    data = bundle_exporter.build_bundle_zip(sel)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert any(n.startswith('images/') for n in names)
    assert any(n.startswith('overlays/') for n in names)
    assert 'labels/annotations_image.csv' in names
    assert 'labels/coco.json' in names
    assert 'manifest.csv' in names
    assert 'README.txt' in names
    # The overlay is a valid PNG with the same dimensions as the source.
    overlay_name = next(n for n in names if n.startswith('overlays/'))
    with PILImage.open(io.BytesIO(zf.read(overlay_name))) as im:
        assert im.size == (IMG_W, IMG_H)
    # Manifest marks the original as included (the file exists on disk).
    assert 'yes' in zf.read('manifest.csv').decode()


# --- HTTP layer: auth gating + real downloads through the blueprint ---

def _login(client, username):
    return client.post('/api/v1/auth/login',
                       json={'username': username, 'password': 'pw'})


def test_export_requires_reviewer_role(app):
    client = app.test_client()
    assert _login(client, 'annotator1').status_code == 200
    resp = client.get('/api/v1/export/coco')
    assert resp.status_code == 403


def test_export_endpoints_download(app):
    client = app.test_client()
    assert _login(client, 'reviewer1').status_code == 200

    summary = client.get('/api/v1/export/summary?status=reviewed')
    assert summary.status_code == 200
    assert summary.get_json()['image_count'] == 1

    cases = [
        ('/api/v1/export/csv?status=reviewed', 'text/csv'),
        ('/api/v1/export/coco?status=reviewed', 'application/json'),
        ('/api/v1/export/yolo?status=reviewed', 'application/zip'),
        ('/api/v1/export/masks?status=reviewed', 'application/zip'),
        ('/api/v1/export/bundle?status=reviewed', 'application/zip'),
    ]
    for url, mimetype in cases:
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert resp.mimetype == mimetype, url
        assert 'attachment' in resp.headers.get('Content-Disposition', '')
        assert len(resp.data) > 0
