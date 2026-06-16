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
