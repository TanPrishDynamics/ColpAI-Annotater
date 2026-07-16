"""Shared enums for annotation values. String-valued so they round-trip JSON cleanly."""
from enum import Enum


class UserRole(str, Enum):
    annotator = 'annotator'
    reviewer = 'reviewer'
    admin = 'admin'


class ImagePhase(str, Enum):
    native = 'native'
    via = 'via'
    vili = 'vili'
    green_filter = 'green_filter'
    unknown = 'unknown'


class MagnificationLevel(str, Enum):
    low = 'low'
    medium = 'medium'
    high = 'high'
    unknown = 'unknown'


class ImageQuality(str, Enum):
    excellent = 'excellent'
    good = 'good'
    fair = 'fair'
    poor = 'poor'


class LightingIssue(str, Enum):
    none = 'none'
    under = 'under'
    over = 'over'
    uneven = 'uneven'


class SCJVisibility(str, Enum):
    fully_visible = 'fully_visible'
    partial = 'partial'
    not_visible = 'not_visible'


class TZType(str, Enum):
    TZ1 = 'TZ1'
    TZ2 = 'TZ2'
    TZ3 = 'TZ3'
    unknown = 'unknown'


class TZVisibility(str, Enum):
    fully_visible = 'fully_visible'
    partial = 'partial'
    not_visible = 'not_visible'


class VascularPattern(str, Enum):
    normal = 'normal'
    fine_punctation = 'fine_punctation'
    coarse_punctation = 'coarse_punctation'
    fine_mosaic = 'fine_mosaic'
    coarse_mosaic = 'coarse_mosaic'
    atypical = 'atypical'


class ColorTone(str, Enum):
    pink = 'pink'
    pale = 'pale'
    dense_white = 'dense_white'
    yellow = 'yellow'


class SurfaceContour(str, Enum):
    smooth = 'smooth'
    micropapillary = 'micropapillary'
    nodular = 'nodular'
    ulcerated = 'ulcerated'


class LesionMargins(str, Enum):
    sharp = 'sharp'
    irregular = 'irregular'


class LesionQuadrant(str, Enum):
    anterior = 'anterior'
    posterior = 'posterior'
    left_lateral = 'left_lateral'
    right_lateral = 'right_lateral'
    circumferential = 'circumferential'


class DiagnosisLabel(str, Enum):
    NORMAL = 'NORMAL'
    CIN1 = 'CIN1'
    CIN2 = 'CIN2'
    CIN3 = 'CIN3'
    AIS = 'AIS'
    INVASIVE_CANCER = 'INVASIVE_CANCER'
    # Benign / non-neoplastic findings.
    INFLAMMATION = 'INFLAMMATION'
    INFECTION = 'INFECTION'
    EROSION = 'EROSION'


class CytologyResult(str, Enum):
    """Referral cervical cytology (Pap smear), Bethesda System categories."""
    NILM = 'NILM'                     # Negative for intraepithelial lesion or malignancy
    ASC_US = 'ASC_US'                 # Atypical squamous cells of undetermined significance
    LSIL = 'LSIL'                     # Low-grade squamous intraepithelial lesion
    ASC_H = 'ASC_H'                   # Atypical squamous cells, cannot exclude HSIL
    HSIL = 'HSIL'                     # High-grade squamous intraepithelial lesion
    AGC = 'AGC'                       # Atypical glandular cells
    AIS = 'AIS'                       # Endocervical adenocarcinoma in situ
    SCC = 'SCC'                       # Squamous cell carcinoma
    unsatisfactory = 'unsatisfactory'


class HPVStatus(str, Enum):
    negative = 'negative'
    positive_16_18 = 'positive_16_18'          # high-risk HPV 16/18
    positive_other_hr = 'positive_other_hr'    # other high-risk type
    positive_unknown = 'positive_unknown'      # positive, type not specified
    not_done = 'not_done'


class ManagementRecommendation(str, Enum):
    routine_recall = 'routine_recall'
    repeat_cytology = 'repeat_cytology'
    colposcopy_followup = 'colposcopy_followup'
    biopsy = 'biopsy'
    excision_leep = 'excision_leep'            # LEEP/LLETZ/cone excision
    refer_oncology = 'refer_oncology'


class IFCPCGrade(str, Enum):
    """IFCPC 2011 per-view colposcopic finding summary."""
    normal = 'normal'
    minor = 'minor'                            # grade 1 (minor) findings
    major = 'major'                            # grade 2 (major) findings
    suspicious_invasion = 'suspicious_invasion'
    miscellaneous = 'miscellaneous'            # e.g. condyloma, polyp, inflammation


class ColposcopyAdequacy(str, Enum):
    adequate = 'adequate'
    inadequate = 'inadequate'


class AnnotationStatus(str, Enum):
    draft = 'draft'
    submitted = 'submitted'
    reviewed = 'reviewed'
    consensus = 'consensus'
    superseded = 'superseded'


class RegionType(str, Enum):
    bbox = 'bbox'
    polygon = 'polygon'
    mask = 'mask'


class ReviewActionType(str, Enum):
    approve = 'approve'
    reject = 'reject'
    edit = 'edit'
