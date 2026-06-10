# ColpAI Annotater — Annotation Guide

## Overview

ColpAI Annotater is a web-based platform for multi-annotator clinical labeling of cervical colposcopy images. It supports image-level annotations (quality, anatomy, diagnosis) and region-level lesion markup (bounding boxes, polygons, segmentation masks), a structured review and consensus workflow, and export to standard ML formats.

---

## Part 1: User Roles

| Role | What They Can Do |
|------|-----------------|
| **Annotator** | Create and submit annotations on assigned images; view own dashboard |
| **Reviewer** | All annotator permissions + approve/reject submitted annotations; view site-wide stats; export data |
| **Admin** | All reviewer permissions + create/disable users; upload images |

### Creating Users (Admin CLI)

```powershell
python -m scripts.create_user --username dr_smith --role annotator --full-name "Dr. Smith"
# Roles: annotator | reviewer | admin
# Password is prompted interactively if omitted
```

---

## Part 2: Getting Started

### 1. Ingest Images

```powershell
python -m scripts.ingest_images --root C:\path\to\images --dataset my_dataset
# --dry-run to preview without inserting
```

Supported formats: `.jpg`, `.jpeg`, `.png`, `.bmp`, `.tif`, `.tiff`  
Images are deduplicated by SHA-256 — re-running is safe.

### 2. Log In

Open `http://localhost:5004` and log in with your username and password.

### 3. Navigate

| Page | URL | Who |
|------|-----|-----|
| Dashboard | `/dashboard` | All roles |
| Annotate | `/annotate` | Annotators |
| Review queue | `/review` | Reviewers, Admins |
| Admin panel | `/admin` | Admins only |

---

## Part 3: Annotation Workflow

### Step 1 — Open an Image

Go to `/annotate`. The platform serves the next unannotated image. You can also navigate directly to `/annotate/{image_id}`.

### Step 2 — Fill in the Annotation Form

The form is grouped into four blocks. Fields marked **\*** are required to submit (draft saves are allowed at any point).

---

### Block A: Image Quality

| Field | Type | Values |
|-------|------|--------|
| `image_quality` | Enum | `excellent`, `good`, `fair`, `poor` |
| `blur_present` | Boolean | |
| `blood_present` | Boolean | |
| `mucus_present` | Boolean | |
| `specular_reflection_present` | Boolean | |
| `lighting_issue` | Enum | `none`, `under`, `over`, `uneven` |
| `usable_for_training` | Boolean | Whether this image should be included in ML exports |

If `usable_for_training` is unchecked, the image is excluded from all exports.

---

### Block B: Anatomy

| Field | Type | Values | Notes |
|-------|------|--------|-------|
| `scj_visibility` | Enum | `fully_visible`, `partial`, `not_visible` | Squamocolumnar junction |
| `transformation_zone_type` | Enum | `TZ1`, `TZ2`, `TZ3`, `unknown` | IFCPC TZ classification |
| `tz_visibility` | Enum | `fully_visible`, `partial`, `not_visible` | |

---

### Block C: Colposcopic Features

| Field | Type | Range / Values | Notes |
|-------|------|----------------|-------|
| `acetowhitening_severity` | Integer | 0 – 3 | 0 = none, 3 = intense white |
| `iodine_pattern` | Integer | 0 – 2 | 0 = normal brown, 2 = mustard yellow (iodine-negative) |
| `vascular_pattern` | Enum | `normal`, `fine_punctation`, `coarse_punctation`, `fine_mosaic`, `coarse_mosaic`, `atypical` | |
| `color_tone` | Enum | `pink`, `pale`, `dense_white`, `yellow` | |
| `surface_contour` | Enum | `smooth`, `micropapillary`, `nodular`, `ulcerated` | |
| `atypical_vessels_present` | Boolean | | Indicates high-grade/invasive disease |

---

### Block D: Diagnosis

| Field | Type | Values | Notes |
|-------|------|--------|-------|
| `colposcopic_impression` **\*** | Enum | `NORMAL`, `CIN1`, `CIN2`, `CIN3`, `AIS`, `INVASIVE_CANCER` | Required to submit |
| `histopathology_result` | Enum | Same as above | Fill in if biopsy result is known |
| `confidence` **\*** | Integer | 1 – 5 | 1 = very uncertain, 5 = certain |
| `notes` | Text | Max 4000 chars | Clinical observations |

---

### Step 3 — Draw Lesion Regions (Optional but Recommended)

For each visible lesion, draw a region on the image using the canvas tools:

**Region types:**
- **Bounding box** — rectangle around the lesion
- **Polygon** — free-form outline (minimum 3 points)
- **Mask** — pixel-level segmentation (RLE or PNG base64)

**Per-region fields:**

| Field | Type | Values / Range |
|-------|------|----------------|
| `lesion_label` | Enum | `NORMAL`, `CIN1`, `CIN2`, `CIN3`, `AIS`, `INVASIVE_CANCER` |
| `lesion_location_clock` | Integer | 1 – 12 (clock position on cervix) |
| `lesion_quadrant` | Enum | `anterior`, `posterior`, `left_lateral`, `right_lateral`, `circumferential` |
| `lesion_size_percent` | Integer | 0 – 100 (% of visible transformation zone) |
| `lesion_margins` | Enum | `sharp`, `irregular` |
| `punctation_present` | Boolean | |
| `punctation_severity` | Integer | 1 – 3 |
| `mosaic_present` | Boolean | |
| `mosaic_severity` | Integer | 1 – 3 |
| `region_notes` | Text | Max 4000 chars |

Multiple regions can be drawn per image. Each region is saved separately and linked to the image annotation.

---

### Step 4 — Save or Submit

| Action | Effect |
|--------|--------|
| **Autosave** | Saves all fields as a draft; you can return and edit later |
| **Submit** | Finalizes the annotation (requires `colposcopic_impression` + `confidence`); locked for editing after submission |
| **Discard** | Marks the image as unusable and records a reason; image is excluded from exports |

---

## Part 4: Annotation Status Lifecycle

```
draft → submitted → reviewed → consensus
                 ↘ rejected → [new draft created, version incremented]
```

| Status | Meaning |
|--------|---------|
| `draft` | In progress; can be autosaved and edited |
| `submitted` | Finalized by annotator; awaiting reviewer action |
| `reviewed` | Approved by a reviewer |
| `consensus` | Computed agreement across multiple reviewers |
| `superseded` | Rejected or discarded; replaced by a new version |

---

## Part 5: Review Workflow

Reviewers see all submitted annotations in the **Review Queue** (`/review`).

### Actions

| Action | Endpoint | Notes |
|--------|----------|-------|
| **Approve** | `POST /api/v1/review/{id}/approve` | Marks annotation as `reviewed`; optional comment |
| **Reject** | `POST /api/v1/review/{id}/reject` | Marks as `superseded`; comment required; creates new draft for annotator |

### Disagreement Detection

`GET /api/v1/review/disagreements` — lists images where multiple annotators submitted different `colposcopic_impression` values. Useful for identifying difficult or ambiguous cases.

---

## Part 6: Consensus Computation

When multiple annotators have reviewed the same image, run:

```powershell
python -m scripts.recompute_consensus
```

This computes an agreement score (0 – 1) across all `reviewed` annotations for each image and writes a `ConsensusLabel` record. Results appear in the dashboard's inter-rater agreement panel.

---

## Part 7: Dashboard & Analytics

The dashboard (`/dashboard`) shows:

**Admins / Reviewers:**
- Total images, total users, active annotators
- Site-wide diagnosis distribution
- Submissions per day per annotator (productivity chart)
- Inter-rater agreement (Cohen's kappa + percent agreement)
- Disagreement count across all images
- Recent submissions feed

**Annotators:**
- Personal submitted / reviewed / draft counts
- Images remaining in queue
- Own diagnosis distribution

---

## Part 8: Exporting Data

Reviewers and admins can export from the **Export** panel or via API.

All exports accept optional query parameters:
- `?dataset=<name>` — filter to a specific ingested dataset
- `?status=reviewed|submitted|all` — default is `reviewed`

| Format | Endpoint | Output |
|--------|----------|--------|
| **CSV (image-level)** | `GET /api/v1/export/csv?level=image` | One row per image; all annotation fields |
| **CSV (region-level)** | `GET /api/v1/export/csv?level=region` | One row per lesion region |
| **COCO Detection** | `GET /api/v1/export/coco` | JSON with bounding boxes and segmentation |
| **YOLO** | `GET /api/v1/export/yolo` | ZIP with `.txt` label files and `data.yaml` |
| **Segmentation Masks** | `GET /api/v1/export/masks` | ZIP with semantic PNG masks (class ID = diagnosis) |
| **Full Bundle** | `GET /api/v1/export/bundle` | ZIP with original images + overlays + label files |
| **Export Summary** | `GET /api/v1/export/summary` | Count preview before downloading |

**COCO / YOLO category mapping:**

| Category ID | Label |
|-------------|-------|
| 0 | NORMAL |
| 1 | CIN1 |
| 2 | CIN2 |
| 3 | CIN3 |
| 4 | AIS |
| 5 | INVASIVE_CANCER |

---

## Part 9: Admin Functions

### User Management (`/admin`)

| Action | Notes |
|--------|-------|
| Create user | Set username, role, full name, password |
| Change role | Promote annotator → reviewer → admin |
| Disable account | Immediately blocks login |
| Reset password | Admin sets new password |

Admins cannot disable their own account or remove their own admin role.

### Uploading Images

Admins can upload individual image files via the admin panel or:

```
POST /api/v1/admin/images/upload   (multipart/form-data)
```

For bulk ingestion, use the CLI script instead (Part 2).

---

## Part 10: Clinical Feature Reference

### Acetowhitening Severity (0 – 3)
- **0 — None:** Normal pink epithelium; no acetowhite reaction
- **1 — Mild:** Faint whitening after acetic acid application
- **2 — Moderate:** Clear, defined white area
- **3 — Intense:** Dense, oyster-white lesion — strong indicator of high-grade pathology

### Iodine Pattern (0 – 2)
- **0 — Normal:** Brown staining; healthy, glycogen-rich squamous epithelium
- **1 — Partial:** Mixed brown and yellow (partial iodine uptake) — suspicious
- **2 — Absent:** Mustard or saffron yellow; no glycogen — iodine-negative lesion, high-grade indicator

### Vascular Pattern
- **normal:** No abnormal vascularity
- **fine_punctation:** Small, evenly spaced dot vessels; lower-grade
- **coarse_punctation:** Large, irregular dot vessels; higher-grade
- **fine_mosaic:** Regular tile-like vascular grid; lower-grade
- **coarse_mosaic:** Irregular, widely spaced mosaic; higher-grade
- **atypical:** Bizarre vessels — corkscrew, hairpin shapes; suggests invasive disease

### Transformation Zone Type (IFCPC)
- **TZ1:** Entirely ectocervical; fully visible
- **TZ2:** Partially endocervical; SCJ visible with aids
- **TZ3:** Entirely endocervical; SCJ not visible

### Lesion Margins
- **sharp:** Well-demarcated, punched-out borders — more characteristic of high-grade
- **irregular:** Geographic, feathered, or satellite lesions — may indicate lower-grade or multifocal disease

### Colposcopic Impression / Diagnosis
- **NORMAL:** No colposcopic abnormality detected
- **CIN1:** Low-grade squamous intraepithelial lesion; usually regresses
- **CIN2:** High-grade CIN; recommended for treatment
- **CIN3:** Severe high-grade CIN / CIS; requires excision
- **AIS:** Adenocarcinoma in situ; endocervical glandular lesion
- **INVASIVE_CANCER:** Frankly invasive cervical carcinoma

### Confidence Score (1 – 5)
- **1:** Very uncertain; ambiguous image or features
- **2:** Low confidence; some features present but unclear
- **3:** Moderate confidence; features consistent but image quality limited
- **4:** High confidence; clear colposcopic features
- **5:** Certain; unambiguous findings or corroborated by histopathology

---

## Part 11: Troubleshooting

### Login fails
- Confirm username is correct (case-sensitive)
- Admin can reset password via `/admin`
- Check account is not disabled (`is_active = false`)

### Annotation form won't submit
- Ensure `colposcopic_impression` and `confidence` are filled in
- Check browser console for validation errors

### Image not appearing in queue
- Image may already have a submitted annotation from this user
- Check `GET /api/v1/images/stats/queue` for remaining count

### Consensus score is missing
- Run `python -m scripts.recompute_consensus` after reviewers have approved multiple annotations on the same image

### Export is empty
- Check `?status=` parameter — default requires `reviewed` annotations
- Verify images have `usable_for_training = true`
- Run `GET /api/v1/export/summary` to preview counts before downloading

### Database errors on startup
- Run `flask --app wsgi db upgrade` to apply any pending Alembic migrations

---

## Part 12: API Quick Reference

All endpoints are under `/api/v1/`. Authentication is session-based (cookie).

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/login` | Log in |
| `POST` | `/auth/logout` | Log out |
| `GET` | `/auth/me` | Current user info |
| `GET` | `/images` | List images (paginated) |
| `GET` | `/images/{id}/file` | Download image file |
| `GET` | `/images/stats/queue` | Queue stats for current user |
| `POST` | `/annotations` | Create or get existing draft |
| `GET` | `/annotations/mine?image_id={id}` | Current user's live annotation |
| `PATCH` | `/annotations/{id}` | Autosave draft fields |
| `POST` | `/annotations/{id}/submit` | Finalize annotation |
| `POST` | `/annotations/{id}/discard` | Discard with reason |
| `POST` | `/annotations/{id}/regions` | Add a lesion region |
| `PATCH` | `/regions/{id}` | Update region |
| `DELETE` | `/regions/{id}` | Delete region |
| `GET` | `/review/queue` | Submitted annotations awaiting review |
| `GET` | `/review/disagreements` | Images with conflicting diagnoses |
| `POST` | `/review/{id}/approve` | Approve annotation |
| `POST` | `/review/{id}/reject` | Reject annotation |
| `GET` | `/dashboard/stats` | Summary statistics |
| `GET` | `/dashboard/agreement` | Inter-rater agreement (kappa) |
| `GET` | `/export/csv` | CSV export |
| `GET` | `/export/coco` | COCO JSON export |
| `GET` | `/export/yolo` | YOLO zip export |
| `GET` | `/export/masks` | Semantic mask zip |
| `GET` | `/export/bundle` | Full bundle zip |
| `GET` | `/admin/users` | List all users |
| `POST` | `/admin/users` | Create user |
| `PATCH` | `/admin/users/{id}` | Update user |
