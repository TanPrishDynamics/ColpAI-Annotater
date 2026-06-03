# ColpAI Annotation Platform - Implementation Plan

## Status

| Phase | Scope | Status |
|---|---|---|
| 1 | DB models, Alembic, ingestion script, auth, image queue API, minimal HTML login + dashboard | DONE |
| 2 | New annotation UI with full schema, autosave, keyboard shortcuts, dark mode | DONE |
| 3 | Konva region tools (bbox / polygon / mask), per-region fields, undo/redo | DONE |
| 4 | Review workflow, consensus computation, dashboard analytics | DONE |
| 5 | Exporters (CSV / COCO / YOLO / segmentation masks) | DONE |

> Status table corrected 2026-06-02: Phases 2-4 were already implemented but still
> marked pending. Phase 5 (exporters) and the Phase 3 mask brush tool were built in
> the same pass. Remaining optimisations (thumbnails on ingest, lazy region-geometry
> loading) are noted in section 7 and are not yet done.

---

## 1. Annotation Schema

The current legacy schema mixes image-level facts (TZ visibility, image phase) with lesion-level facts (punctation severity, margins) on a single flat row. That blocks multi-lesion images and forces the annotator to summarize when there are 2+ lesions of different grades. Reorganized into three layers:

### Layer A - Image (intrinsic, set once during ingestion)

| Field | Type | Notes |
|---|---|---|
| `image_id` | str (UUID) | stable, persisted, never reused |
| `source_path` | str | absolute path, immutable |
| `sha256` | str | dedup key - same image in two folders = one row |
| `dataset_source` | str | `intel_mobileodt` / `kaggle_v4` / `cin_123` / etc. |
| `capture_device` | str | nullable, free text |
| `image_resolution` | str | `WxH`, computed on ingest |
| `magnification_level` | enum | `low` / `medium` / `high` / `unknown` |
| `image_phase` | enum | `native` / `via` / `vili` / `green_filter` |
| `width_px`, `height_px` | int | for region coordinate validation |

### Layer B - Image-level annotation (one row per image x annotator x version)

Grouped into UI accordion sections:

**Quality assessment**
- `image_quality` - enum: `excellent` / `good` / `fair` / `poor`
- `blur_present`, `blood_present`, `mucus_present`, `specular_reflection_present` - bool
- `lighting_issue` - enum: `none` / `under` / `over` / `uneven`
- `usable_for_training` - bool (derived suggestion: false if quality=poor)

**Anatomy**
- `scj_visibility` - enum: `fully_visible` / `partial` / `not_visible`
- `transformation_zone_type` - enum: `TZ1` / `TZ2` / `TZ3` / `unknown`
- `tz_visibility` - enum (kept for backward compat)

**Global colposcopic features** (whole-image, not lesion-specific)
- `acetowhitening_severity` - int 0-3
- `iodine_pattern` - int 0-2
- `vascular_pattern` - enum: `normal` / `fine_punctation` / `coarse_punctation` / `fine_mosaic` / `coarse_mosaic` / `atypical`
- `color_tone` - enum: `pink` / `pale` / `dense_white` / `yellow`
- `surface_contour` - enum: `smooth` / `micropapillary` / `nodular` / `ulcerated`
- `atypical_vessels_present` - bool

**Diagnosis**
- `colposcopic_impression` - enum: `NORMAL` / `CIN1` / `CIN2` / `CIN3` / `AIS` / `INVASIVE_CANCER`
- `histopathology_result` - same enum, nullable (rarely available)
- `confidence` - int 1-5
- `notes` - str

**Status**
- `annotator_id` - FK
- `status` - enum: `draft` / `submitted` / `reviewed` / `consensus` / `superseded`
- `created_at`, `updated_at`, `submitted_at` - timestamps
- `version` - int (incremented on each submit; old rows kept)

### Layer C - Region annotation (zero-to-many per image-annotation)

Replaces the per-image `punctation_*` / `mosaic_*` / `lesion_*` fields when the annotator wants to mark specific lesions:

- `region_id` - UUID
- `image_annotation_id` - FK
- `region_type` - enum: `bbox` / `polygon` / `mask`
- `geometry` - JSON (see section 6)
- `lesion_label` - enum (same as colposcopic_impression)
- `lesion_location_clock` - int 1-12
- `lesion_quadrant` - enum: `anterior` / `posterior` / `left_lateral` / `right_lateral` / `circumferential`
- `lesion_size_percent` - int 0-100
- `lesion_margins` - enum: `sharp` / `irregular`
- `punctation_present`, `punctation_severity` - bool, int 1-3
- `mosaic_present`, `mosaic_severity` - bool, int 1-3
- `region_notes` - str

A multi-lesion image now annotates correctly, exports cleanly to COCO/YOLO, and gives the model per-region training targets instead of one fuzzy global label.

---

## 2. Database Models (SQLAlchemy + SQLite)

```
User              (id, username, role[annotator|reviewer|admin], created_at, last_login)
Image             (id, sha256 UNIQUE, source_path, dataset_source, image_phase,
                   capture_device, magnification_level, width_px, height_px,
                   resolution, ingested_at)
ImageAnnotation   (id, image_id FK, annotator_id FK, status, version,
                   <all Layer B fields>, created_at, updated_at, submitted_at)
Region            (id, image_annotation_id FK, region_type, geometry JSON,
                   <all Layer C fields>, created_at, updated_at)
ConsensusLabel    (id, image_id FK, label, derived_from JSON[annotation_ids],
                   agreement_score, computed_at)
DiscardedImage    (id, image_id FK, annotator_id FK, reason, discarded_at)
ReviewAction      (id, image_annotation_id FK, reviewer_id FK,
                   action[approve|reject|edit], comment, created_at)
AuditLog          (id, user_id, entity_type, entity_id, action, diff JSON, created_at)
```

**Indexes:** `Image.sha256`, `ImageAnnotation(image_id, annotator_id, version)`, `Region.image_annotation_id`, `AuditLog(entity_type, entity_id)`.

**Versioning strategy:** every "submit" creates a new `ImageAnnotation` row with `version = max+1`; the previous row's status flips to `superseded`. Drafts are mutated in place. Cheap, gives full history, no separate `*_versions` table.

**Migrations:** Alembic from day one, via Flask-Migrate.

---

## 3. Backend API Structure

Flask app factory + blueprints. Pydantic for request/response validation.

```
POST   /api/v1/auth/login                     (DONE)
POST   /api/v1/auth/logout                    (DONE)
GET    /api/v1/auth/me                        (DONE)

GET    /api/v1/images?phase=via&status=...    (DONE)
GET    /api/v1/images/{id}                    (DONE)
GET    /api/v1/images/{id}/file               (DONE)
GET    /api/v1/images/datasets                (DONE)
GET    /api/v1/images/stats/queue             (DONE)
POST   /api/v1/images/ingest                  (Phase 2 - currently CLI-only)

GET    /api/v1/annotations?image_id=...       (Phase 2)
POST   /api/v1/annotations                    (Phase 2)
PATCH  /api/v1/annotations/{id}               (Phase 2 - autosave)
POST   /api/v1/annotations/{id}/submit        (Phase 2)
POST   /api/v1/annotations/{id}/discard       (Phase 2)

POST   /api/v1/annotations/{id}/regions       (Phase 3)
PATCH  /api/v1/regions/{id}                   (Phase 3)
DELETE /api/v1/regions/{id}                   (Phase 3)

GET    /api/v1/review/queue                   (Phase 4)
POST   /api/v1/review/{annotation_id}/approve (Phase 4)
POST   /api/v1/review/{annotation_id}/reject  (Phase 4)
GET    /api/v1/review/disagreements           (Phase 4)

GET    /api/v1/export/summary?dataset=...     (DONE - counts preview for UI)
GET    /api/v1/export/csv?dataset=...&level=  (DONE - image|region level)
GET    /api/v1/export/coco?dataset=...        (DONE)
GET    /api/v1/export/yolo?dataset=...        (DONE - zip of labels + data.yaml)
GET    /api/v1/export/masks?dataset=...       (DONE - zip of PNG masks)

GET    /api/v1/dashboard/stats                (Phase 4)
GET    /api/v1/dashboard/agreement            (Phase 4)
GET    /api/v1/dashboard/distribution         (Phase 4)
GET    /api/v1/dashboard/productivity         (Phase 4)
```

**Validation rules** enforced server-side at submit time (not just draft save):
- conditional fields: `punctation_severity` required iff `punctation_present == true`
- enum membership for every dropdown
- region geometry within image bounds
- `colposcopic_impression` required for `submit`
- `confidence` required for `submit`

**Error format:** consistent JSON `{ "error": { "code": "...", "message": "...", "details": {...} } }`.

---

## 4. Frontend Annotation Workflow (Phase 2 + 3)

### Page layout

```
+---------------------------------------------------------------+
| Top bar: ColpAI . annotator name . queue progress . dark mode |
+----------------------------------+----------------------------+
|                                  | Image metadata (collapsed) |
|                                  +----------------------------+
|   Konva canvas                   | Quality                    |
|   - image layer                  +----------------------------+
|   - regions layer (drawn shapes) | Anatomy                    |
|   - tool overlay (cursor, guides)| Global features            |
|                                  | Diagnosis (always visible) |
|                                  +----------------------------+
| Tool dock:                       | Region list (selectable)   |
| [pan] [bbox] [poly] [mask] [u][r]| Per-region panel           |
+----------------------------------+----------------------------+
| Footer: Discard . Skip . Save Draft . Submit & Next ->        |
+---------------------------------------------------------------+
```

### Konva.js stage architecture
- **Stage** with three Layers: `imageLayer` (one Konva.Image), `regionLayer` (Konva.Group per region), `toolLayer` (transient drawing/guides).
- **Bbox tool**: mousedown -> drag -> mouseup creates Konva.Rect with Konva.Transformer (resize handles).
- **Polygon tool**: click-to-add-vertex, double-click to close, vertices editable via draggable circles.
- **Mask tool**: paint into an offscreen canvas at native image resolution; serialize as RLE on save.
- **Zoom/pan**: wheel zooms around cursor, space+drag pans. Scale stays bounded to [0.25x, 8x].
- **Brightness/contrast**: CSS `filter` on the Konva.Image node - non-destructive, no canvas redraw cost.
- **Undo/redo**: a command stack of `{do, undo}` ops applied to the regionLayer. Limit 50.

### Keyboard shortcuts (default)

| Key | Action |
|---|---|
| `1`-`6` | set diagnosis: NORMAL/CIN1/CIN2/CIN3/AIS/INVASIVE |
| `B` / `P` / `M` | bbox / polygon / mask tool |
| `V` | pan/select |
| `Ctrl+Z` / `Ctrl+Shift+Z` | undo / redo |
| `Del` | delete selected region |
| `[` / `]` | prev / next image |
| `Ctrl+S` | save draft |
| `Enter` | submit & next |
| `Esc` | cancel current tool / close fullscreen |
| `?` | show shortcut overlay |

### Autosave behavior
- Debounced 800ms after any field change -> `PATCH /annotations/{id}`.
- Visible status pill: `Saved` / `Saving...` / `Unsaved` / `Error - retry`.
- Browser `beforeunload` warning if status = `Unsaved`.
- Drafts survive crashes - server stores incremental state.

### Server-side image queue (replaces session counter)
The legacy `session['current_pos']` bug (image changes on Exit Crop) is gone because the next-image decision moved server-side: `GET /api/v1/images?status=unannotated&for=me` returns a sorted list, the UI tracks position, and Exit Crop is purely a canvas op.

---

## 5. UI/UX Recommendations

- **One screen, no scrolling for diagnosis.** The diagnosis radio + confidence slider must always be visible. Everything else collapses into accordion groups.
- **Conditional fields hide cleanly** - don't grey out `punctation_severity`, hide it entirely when `punctation_present=false`. Less visual noise.
- **Region list = source of truth.** Selecting a row in the list highlights the shape on canvas; selecting a shape highlights its row. Single-source-of-truth prevents UI drift.
- **Color-code regions by label** (NORMAL=green, CIN1=yellow, CIN2=orange, CIN3=red, AIS=purple, INVASIVE=dark red). Same palette in dashboard charts.
- **Dark mode via CSS variables** - `--bg`, `--fg`, `--accent`. One stylesheet, toggle a `data-theme` attribute on `<body>`. Medical images look noticeably different on dark backgrounds, so let the user choose.
- **Brightness/contrast as sliders** that reset on next image. Don't persist - it's a viewing aid, not part of the annotation.
- **Progress ring in the top bar**: "47 / 1,200 images . 4 in review . 3 disagreements." Gives a sense of momentum.
- **"Why this label?" hint card** for each enum option (mini reference card on hover) - reduces clinician friction.
- **Disagreement flag** on the image card if other annotators have submitted different impressions - primes the annotator before they look.

---

## 6. Example Annotation JSON

```json
{
  "annotation_id": "a1f2c3d4-...",
  "version": 2,
  "status": "submitted",
  "image": {
    "image_id": "img_5e8b...",
    "source_path": "/Volumes/.../IMG_0142.jpg",
    "sha256": "9f2b...",
    "dataset_source": "kaggle_v4",
    "image_phase": "via",
    "capture_device": "Olympus OCS-500",
    "magnification_level": "medium",
    "image_resolution": "1920x1080",
    "width_px": 1920,
    "height_px": 1080
  },
  "annotator_id": "u_dr_smith",
  "submitted_at": "2026-05-11T10:48:22Z",
  "quality": {
    "image_quality": "good",
    "blur_present": false,
    "blood_present": true,
    "mucus_present": false,
    "lighting_issue": "none",
    "specular_reflection_present": true,
    "usable_for_training": true
  },
  "anatomy": {
    "scj_visibility": "fully_visible",
    "transformation_zone_type": "TZ2",
    "tz_visibility": "fully_visible"
  },
  "features": {
    "acetowhitening_severity": 2,
    "iodine_pattern": 1,
    "vascular_pattern": "coarse_mosaic",
    "color_tone": "dense_white",
    "surface_contour": "micropapillary",
    "atypical_vessels_present": false
  },
  "diagnosis": {
    "colposcopic_impression": "CIN2",
    "histopathology_result": null,
    "confidence": 4,
    "notes": "Dense acetowhite at 12 o'clock, sharp margins."
  },
  "regions": [
    {
      "region_id": "r_71...",
      "region_type": "polygon",
      "geometry": {
        "points": [[820, 410], [1180, 395], [1240, 690], [870, 720]]
      },
      "lesion_label": "CIN2",
      "lesion_location_clock": 12,
      "lesion_quadrant": "anterior",
      "lesion_size_percent": 18,
      "lesion_margins": "sharp",
      "punctation_present": true,
      "punctation_severity": 2,
      "mosaic_present": true,
      "mosaic_severity": 3,
      "region_notes": "Coarse mosaic visible centrally."
    },
    {
      "region_id": "r_72...",
      "region_type": "bbox",
      "geometry": {"x": 1310, "y": 540, "w": 220, "h": 180},
      "lesion_label": "CIN1",
      "lesion_location_clock": 3,
      "lesion_quadrant": "right_lateral",
      "lesion_size_percent": 4,
      "lesion_margins": "irregular",
      "punctation_present": false,
      "mosaic_present": false
    }
  ]
}
```

Mask regions store geometry as `{ "format": "rle", "size": [h, w], "counts": "..." }` (COCO-compatible RLE) so COCO export is a direct passthrough.

---

## 7. Project Architecture

```
ColpAi-Annotater/
├── app/
│   ├── __init__.py              # create_app(), blueprint registration
│   ├── config.py                # dev / prod / test configs
│   ├── extensions.py            # db, login_manager, migrate
│   ├── models/
│   │   ├── enums.py             (DONE)
│   │   ├── user.py              (DONE)
│   │   ├── image.py             (DONE)
│   │   ├── annotation.py        (DONE)
│   │   ├── region.py            (DONE)
│   │   ├── review.py            (DONE)
│   │   └── audit.py             (DONE)
│   ├── schemas/                 # Pydantic request/response models
│   │   ├── auth.py              (DONE)
│   │   ├── image.py             (DONE)
│   │   ├── annotation.py        (Phase 2)
│   │   └── region.py            (Phase 3)
│   ├── api/                     # JSON blueprints
│   │   ├── auth.py              (DONE)
│   │   ├── images.py            (DONE)
│   │   ├── annotations.py       (Phase 2)
│   │   ├── regions.py           (Phase 3)
│   │   ├── review.py            (Phase 4)
│   │   ├── export.py            (Phase 5)
│   │   └── dashboard.py         (Phase 4)
│   ├── views/                   # HTML routes (login, annotate, dashboard)
│   │   └── pages.py             (DONE - placeholder)
│   ├── services/                # business logic, no Flask imports
│   │   ├── ingestion.py         (DONE)
│   │   ├── consensus.py         (Phase 4)
│   │   ├── audit.py             (Phase 4)
│   │   └── exporters/
│   │       ├── selection.py     (DONE - shared "what to export" logic)
│   │       ├── geometry.py      (DONE - bbox/polygon/mask + numpy RLE)
│   │       ├── csv_exporter.py  (DONE)
│   │       ├── coco_exporter.py (DONE)
│   │       ├── yolo_exporter.py (DONE)
│   │       └── mask_exporter.py (DONE)
│   ├── templates/
│   │   ├── base.html            (DONE - dark-mode CSS vars, layout shell)
│   │   ├── login.html           (DONE)
│   │   ├── dashboard.html       (DONE - placeholder)
│   │   ├── annotate.html        (Phase 2)
│   │   └── review.html          (Phase 4)
│   └── static/                  (Phase 2)
│       ├── css/                 # one app.css with CSS vars
│       └── js/
│           ├── annotator.js     # main controller
│           ├── konva-tools.js   # bbox/polygon/mask tool implementations
│           ├── undo-stack.js
│           ├── shortcuts.js
│           └── autosave.js
├── migrations/                  (DONE - via Flask-Migrate)
├── scripts/
│   ├── ingest_images.py         (DONE)
│   ├── create_user.py           (DONE)
│   └── recompute_consensus.py   (Phase 4)
├── tests/                       (Phase 2+ as features land)
├── data/
│   ├── annotations.db           (DONE - SQLite)
│   └── masks/                   (Phase 3 - binary PNG masks)
├── exports/                     (Phase 5)
├── requirements.txt             (DONE)
├── wsgi.py                      (DONE)
└── PLAN.md                      (this file)
```

**Library choices:**
- `flask`, `flask-sqlalchemy`, `flask-login`, `flask-migrate` (DONE)
- `pydantic` for validation (cleaner than marshmallow for nested schemas) (DONE)
- `pillow` + `numpy` for image dims, mask manipulation (Pillow DONE; numpy in Phase 3)
- `pycocotools` for COCO RLE encode/decode (Phase 5)
- `cropperjs` (already in legacy tool) - keep for the simple crop, but cropper is a sub-tool now, not the main interaction
- `konva` - region drawing (Phase 3)
- No frontend framework needed (vanilla JS modules + a small event bus). Adding React/Vue would 2x the build complexity for a tool with one main page.

**Performance notes for large datasets:**
- Stream images via `send_file` with `conditional=True` for HTTP range support -> fast browser caching. (DONE)
- Generate thumbnails on ingest (e.g., 400px wide) for queue/dashboard views - never load full images for lists. (Phase 2)
- Lazy-load region geometry (image-level annotation returns region IDs + counts only; UI fetches geometry on demand). (Phase 3)
- Index `Image.sha256` and `(ImageAnnotation.image_id, status)` - these are the hot queries. (DONE)
- Pagination via cursor (id-based), not offset - offset breaks at 100K+ rows. (DONE)

---

## What's not in this plan (flag if you want any added)

- Authentication beyond username session (no SSO, MFA, password reset).
- Image upload UI - current flow scans existing folders. Add an upload endpoint if clinicians will contribute images directly.
- Realtime collaboration (two annotators on same image at once). Out of scope for a research tool.
- Active learning / model-in-the-loop suggestions. Possible Phase 6 once you have a baseline model.
- Cloud deployment, Docker, CI. Local-only assumption.
