"""Re-export models so `from app.models import X` works and Alembic discovers them."""
from app.models.user import User
from app.models.image import Image
from app.models.annotation import ImageAnnotation
from app.models.region import Region
from app.models.review import ReviewAction, DiscardedImage, DiagnosisReviewAction
from app.models.patient import PatientDiagnosis, PatientConsensus
from app.models.audit import AuditLog

__all__ = [
    'User',
    'Image',
    'ImageAnnotation',
    'Region',
    'ReviewAction',
    'DiscardedImage',
    'DiagnosisReviewAction',
    'PatientDiagnosis',
    'PatientConsensus',
    'AuditLog',
]
