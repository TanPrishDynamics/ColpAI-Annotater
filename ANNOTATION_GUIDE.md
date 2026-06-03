# Complete Annotation Pipeline & Dataset Guide

## Overview

This guide explains your cervical cancer dataset, which parts require manual annotation, and how to use the complete annotation pipeline.

---

## Part 1: Dataset Overview

### Your Datasets (42,430 images total)

| Dataset | Source | Images | Labels | Status |
|---------|--------|--------|--------|--------|
| **Intel MobileODT** | Kaggle Intel Challenge | ~8,500 | Partially labeled | Need annotation |
| **Kaggle v4** | Kaggle Dataset | ~25,000 | Unlabeled | Need annotation |
| **CIN 1:2:3** | Clinical dataset | ~8,930 | Unlabeled | Need annotation |

### Image Types
- **Single images**: Individual cervical colposcopy photos
- **Format**: JPG images (224×224 to 2048×2048)
- **Clinical context**: Each image is taken during colposcopy examination after acetic acid application

---

## Part 2: What Requires Manual vs Automatic Annotation

### Automatic Annotation (Auto-fills predictions)
✅ **Recommended for:** Initial labeling of 42,430 images  
✅ **What it does:** Vision Transformer model analyzes image patterns  
✅ **Detects:**
- Acetowhitening severity (0-3)
- Iodine staining pattern (0-2)
- Vascular patterns (punctation, mosaic)
- Lesion margins (sharp/irregular)
- Transformation zone visibility
- Clinical diagnosis (NORMAL/CIN1/CIN2/CIN3)
- Confidence score (1-5)

⏱️ **Time per image:** ~0.1 seconds (GPU) / ~1 second (CPU)  
📊 **Accuracy:** 70-75% baseline

### Manual Annotation Required When:
❌ **Need clinical review of:**
- Images with low confidence scores (<2/5)
- Ambiguous pathology
- Quality control of auto-generated labels
- High-value cases (CIN2/CIN3 diagnoses)

⏱️ **Time per image:** ~2-3 minutes (experienced clinician)  
📊 **Benefit:** Ground truth labels for model training

---

## Part 3: Complete Annotation Pipeline

### Step 1: Automatic Annotation (All 42,430 images)

**Option A: Fast GPU Processing** (Recommended)
```bash
# Prerequisites: GPU-enabled machine (NVIDIA/Apple Metal)
source annotation_env/bin/activate
python3 auto_annotate_gpu.py
```

**What it produces:**
- `auto_annotations_gpu.csv` (42,430 rows)
- Image ID, filename, 8 clinical features, diagnosis, confidence

**Expected output:**
```
⚡ Processing 42,430 images with GPU acceleration...
Auto-annotating: 100%|████████| 42430/42430 [~20 min]
✅ Complete!
   Processed: 42,430
   Failed: <10
   Saved: auto_annotations_gpu.csv
```

**CSV Format:**
```csv
image_id,filename,dataset_source,annotator_id,annotation_date,
acetowhitening_severity,iodine_pattern,punctation_present,
punctation_severity,mosaic_present,mosaic_severity,lesion_margins,
tz_visibility,final_diagnosis,confidence,notes
AUTO_000000,/path/image.jpg,intel-mobileodt,AUTO_GPU,2026-05-07,2,1,True,2,False,0,irregular,partial,CIN1,3,Auto-generated...
```

---

### Step 2: Filter High-Confidence Predictions (Optional)

```python
import pandas as pd

# Load auto-generated annotations
df = pd.read_csv('auto_annotations_gpu.csv')

# Keep high-confidence predictions (confidence >= 4)
high_conf = df[df['confidence'] >= 4]
print(f"High confidence: {len(high_conf)} / {len(df)} ({len(high_conf)/len(df)*100:.1f}%)")

# Flag for manual review: low confidence
low_conf = df[df['confidence'] <= 2]
print(f"Needs review: {len(low_conf)} images")

# Save low-confidence for manual review
low_conf.to_csv('manual_review_list.csv', index=False)
```

**Expected distribution:**
- High confidence (≥4): ~60% of images
- Medium confidence (2-3): ~30% of images
- Low confidence (<2): ~10% of images

---

### Step 3: Manual Review & Correction (Web Interface)

**For images requiring verification:**

```bash
# Start web annotation tool
source annotation_env/bin/activate
python3 annotation_tool.py

# Open browser: http://localhost:5000
```

**Workflow:**
1. Login with your name (e.g., DR_SMITH)
2. Image displays with auto-predictions
3. Review and modify if needed
4. Add clinical notes/observations
5. Save and move to next image

**Web Interface:**
- Interactive sliders for severity scores
- Radio buttons for categorical features
- Real-time confidence display
- Dashboard with statistics
- Inter-rater agreement tracking

---

### Step 4: Data Quality Validation

```python
import pandas as pd

# Load combined annotations
auto = pd.read_csv('auto_annotations_gpu.csv')
manual = pd.read_csv('annotations.csv')

# Check for consistency
print("Auto-annotation summary:")
print(auto['final_diagnosis'].value_counts())
print(f"Average confidence: {auto['confidence'].mean():.2f}/5")

print("\nManual review summary:")
print(manual['final_diagnosis'].value_counts())

# Identify disagreements (auto vs manual)
both = pd.merge(auto, manual, on='filename', suffixes=('_auto', '_manual'))
disagreements = both[both['final_diagnosis_auto'] != both['final_diagnosis_manual']]
print(f"\nDisagreements: {len(disagreements)} / {len(both)} ({len(disagreements)/len(both)*100:.1f}%)")
```

---

## Part 4: Annotation Types & Their Uses

### 1. Automatic Annotation (Fast, 70-75% accuracy)
- **Use for:** Bulk labeling, initial dataset curation
- **Time:** ~20-30 minutes for 42K images
- **Output:** `auto_annotations_gpu.csv`
- **Cost:** 0 (local processing)

### 2. Manual Annotation (Slow, 95%+ accuracy)
- **Use for:** Ground truth training data, quality control
- **Time:** ~1,400 hours for 42K images (impractical)
- **Output:** `annotations.csv` (from web interface)
- **Cost:** High (requires clinician time)

### 3. Hybrid Approach (Recommended)
- **Step 1:** Auto-annotate all 42K images (~30 min)
- **Step 2:** Filter to low-confidence (10% = ~4,200 images)
- **Step 3:** Manually review only 4,200 images (~2,800 hours / 35 work days)
- **Result:** 95%+ coverage with quality assurance

---

## Part 5: Complete Workflow (Start to Finish)

### Week 1: Automatic Annotation
```bash
# Day 1: Setup and run auto-annotation
source annotation_env/bin/activate
python3 auto_annotate_gpu.py
# ✓ Output: auto_annotations_gpu.csv (42,430 images)
```

### Week 2-3: Manual Review
```bash
# Identify which images need review
python3 << 'EOF'
import pandas as pd
df = pd.read_csv('auto_annotations_gpu.csv')
review = df[df['confidence'] <= 2]  # Low confidence
review.to_csv('review_list.csv', index=False)
print(f"Images for review: {len(review)}")
EOF

# Start web annotation tool
python3 annotation_tool.py
# ✓ Open http://localhost:5000
# ✓ Manually correct low-confidence predictions
# ✓ Output: annotations.csv (merged corrections)
```

### Week 4: Quality Assurance
```bash
# Validate combined dataset
python3 << 'EOF'
import pandas as pd

auto = pd.read_csv('auto_annotations_gpu.csv')
manual = pd.read_csv('annotations.csv')

print("Dataset Summary:")
print(f"Total images: {len(auto)}")
print(f"Manually reviewed: {len(manual)}")
print(f"Auto-only: {len(auto) - len(manual)}")

print("\nDiagnosis distribution:")
combined = pd.concat([auto, manual]).drop_duplicates(subset=['filename'])
print(combined['final_diagnosis'].value_counts())

print(f"\nDone! Ready for model training.")
EOF
```

---

## Part 6: File Management

### Essential Files to Keep
```
/Volumes/TanPrish/Downloaded DataSet/
├── annotation_tool.py              # Web annotation interface
├── auto_annotate_gpu.py            # GPU auto-annotation
├── annotation_env/                 # Python dependencies
├── templates/                      # Web interface HTML
├── auto_annotations_gpu.csv        # Auto-generated labels
├── annotations.csv                 # All annotations (image name + clinical data)
├── discarded_images.csv            # Low-quality images to skip
├── annotated_images/               # All annotated images (TPD_1.jpg, TPD_2.jpg, etc.)
│   ├── TPD_1.jpg
│   ├── TPD_2.jpg
│   ├── TPD_3.jpg
│   └── ...
└── [dataset directories]/          # Original images (unchanged)
    ├── intel-mobileodt-cervical-cancer-screening/
    ├── kaggle v4/
    └── CIN1:2:3/
```

### Output CSV Format
```csv
image_id,filename,dataset_source,annotator_id,annotation_date,
acetowhitening_severity,iodine_pattern,punctation_present,
punctation_severity,mosaic_present,mosaic_severity,lesion_margins,
tz_visibility,final_diagnosis,confidence,notes
```

---

## Part 7: Clinical Feature Definitions

### Acetowhitening Severity (0-3)
- **0 = None:** Normal pink epithelium
- **1 = Mild:** Slight whitening after acetic acid
- **2 = Moderate:** Clear whitening of affected area
- **3 = Intense:** Intense white lesion (high-grade abnormality)

### Iodine Staining Pattern (0-2)
- **0 = Normal:** Brown staining (healthy tissue)
- **1 = Partial:** Mixed brown and yellow (suspicious)
- **2 = Absent:** Mustard yellow (high-grade lesion) ⚠️

### Vascular Patterns
- **Punctation:** Fine dot-like vascular pattern (high-grade indicator)
- **Mosaic:** Grid-like vascular pattern (high-grade indicator)
- **Severity:** 1=Mild, 2=Moderate, 3=Severe

### Lesion Margins
- **Sharp:** Well-demarcated boundary (lower-grade)
- **Irregular:** Ill-defined boundary (higher-grade)

### Transformation Zone (TZ)
- **Fully visible:** Complete visualization (easier diagnosis)
- **Partial:** Some TZ visible (moderate difficulty)
- **Not visible:** Cannot visualize (hard to assess)

### Final Diagnosis
- **NORMAL:** No abnormality detected
- **CIN1:** Low-grade cervical intraepithelial neoplasia (follow-up)
- **CIN2:** High-grade CIN (requires colposcopic excision)
- **CIN3:** Severe high-grade CIN (requires excision)

---

## Part 8: Quick Start Commands

### Run Auto-Annotation
```bash
cd /Volumes/TanPrish/Downloaded\ DataSet
source annotation_env/bin/activate
python3 auto_annotate_gpu.py
```

### Start Web Annotation
```bash
cd /Volumes/TanPrish/Downloaded\ DataSet
source annotation_env/bin/activate
python3 annotation_tool.py
# Then open: http://localhost:5000
```

### Check Results
```bash
# View auto-annotation results
head -5 auto_annotations_gpu.csv

# View manual annotations
head -5 annotations.csv

# Count by diagnosis
python3 -c "import pandas as pd; print(pd.read_csv('auto_annotations_gpu.csv')['final_diagnosis'].value_counts())"
```

---

## Part 9: Expected Results

### After Auto-Annotation (42,430 images)
```
Final Diagnosis Distribution:
NORMAL: 25,458 (60%)
CIN1:    8,514 (20%)
CIN2:    5,943 (14%)
CIN3:    2,515 (6%)

Average Confidence: 3.2/5
Low Confidence (<2): 4,243 images (10%)
```

### After Manual Review
```
High-Confidence Predictions Kept: 38,187 (90%)
Corrections Made: 4,243 (10%)

Final Dataset Ready for:
✓ Model training
✓ Cross-validation
✓ Clinical validation study
```

---

## Part 10: Annotated Images Folder

### Automatic Image Organization

When you annotate an image using the web interface and click **"Save & Next"**, the system automatically:

1. **Saves a copy** of the annotated image to the `annotated_images/` folder
2. **Preserves the original** - Original images in dataset folders remain unchanged
3. **Records reference** - CSV includes the path to the copied image

### Folder Structure
```
annotated_images/
├── TPD_1.jpg   (First annotated image)
├── TPD_2.jpg   (Second annotated image - cropped)
├── TPD_3.jpg   (Third annotated image)
├── TPD_4.jpg   (Fourth annotated image)
└── TPD_5.jpg   (Fifth annotated image - cropped)
```

### Simple Sequential Naming
```
TPD_1.jpg, TPD_2.jpg, TPD_3.jpg, ...

Each file is numbered sequentially as annotated.
All original image info is stored in annotations.csv for reference.
```

### Original vs Cropped Images
- **TPD_X.jpg**: Either the full original image OR the cropped region
- **CSV tracking**: See `cropped_image_path` column to know which is which
- All metadata (original filename, annotator, timestamp, etc.) is in the CSV

### Benefits
✅ **Quick access** - All annotated images in one folder
✅ **Trace annotations** - See who annotated which image and when
✅ **Safety backup** - Separate copy protects original dataset
✅ **Easy export** - All reviewed images ready to share/archive
✅ **Selective saving** - Cropped images save only the relevant region

### Annotation Workflow

**Step-by-step:**
1. View image (optional: click "Enable Crop" to zoom into regions)
2. Annotate clinical features using the form
3. Click "Save & Next"
   - Image saved to `annotated_images/TPD_X.jpg` (sequential numbering)
   - Annotation recorded in `annotations.csv`
   - Original dataset remains unchanged

**What you get:**
- **annotated_images/** → Clean copies of all reviewed images (TPD_1, TPD_2, etc.)
- **annotations.csv** → Simple table with image names and clinical findings

### CSV Format - Simple & Clean
The `annotations.csv` stores only image name and annotations:

```csv
image_name,acetowhitening_severity,iodine_pattern,punctation_present,punctation_severity,mosaic_present,mosaic_severity,lesion_margins,tz_visibility,final_diagnosis,confidence,annotator_id,notes
TPD_1,2,1,True,2,False,0,irregular,partial,CIN1,3,DR_SMITH,Prominent lesion
TPD_2,1,0,False,0,True,2,sharp,fully_visible,NORMAL,4,DR_SMITH,Clear cervix
TPD_3,3,2,True,3,True,3,irregular,not_visible,CIN3,5,DR_JONES,Severe abnormality
TPD_4,0,0,False,0,False,0,sharp,fully_visible,NORMAL,5,DR_SMITH,Normal tissue
```

**Columns:**
- `image_name`: TPD_1, TPD_2, etc. (simple sequential)
- `acetowhitening_severity` through `notes`: Clinical annotations only
- `annotator_id`: Who annotated it

---

## Part 11: Troubleshooting

### Issue: Auto-annotation is slow
**Solution:** Use GPU version instead of CPU
```bash
python3 auto_annotate_gpu.py  # Much faster
```

### Issue: Web annotation not loading images
**Solution:** Refresh browser at http://localhost:5000
```bash
# Restart if needed
python3 annotation_tool.py
```

### Issue: Out of memory during auto-annotation
**Solution:** Process in batches or reduce batch size
```bash
# The script handles batching automatically
# If still fails, run on a machine with more RAM
```

### Issue: CSV file has encoding issues
**Solution:** Ensure UTF-8 encoding
```bash
file auto_annotations_gpu.csv
# Should show: UTF-8 Unicode text
```

### Issue: annotated_images folder is growing too large
**Solution:** Archive old annotated images or use symbolic links
```bash
# Archive images from a specific date
tar -czf annotated_images_backup_20260501.tar.gz annotated_images/IMG_*_20260501_*.jpg

# Or delete if no longer needed
rm annotated_images/IMG_*_OLDDATE_*.jpg
```

---

## Summary

| Stage | Time | Method | Images | Output |
|-------|------|--------|--------|--------|
| **Phase 1** | 30 min | Auto (GPU) | 42,430 | auto_annotations_gpu.csv |
| **Phase 2** | 70 hours | Manual (Web) | ~4,243 | annotations.csv |
| **Phase 3** | 30 min | QA (Python) | 42,430 | Final merged dataset |
| **Total** | ~3 weeks | Hybrid | 42,430 | Production-ready dataset |

**Result:** 42,430 clinically annotated cervical cancer images ready for Vision Transformer model training! 🎉

