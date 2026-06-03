"""Image ingestion service.

Scans one or more directories, computes a sha256 for each image, reads dimensions,
and creates Image rows. Idempotent: re-running on the same directory only adds
files whose sha256 isn't already in the DB.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image as PILImage

from app.extensions import db
from app.models import Image
from app.models.enums import ImagePhase

CHUNK_SIZE = 1024 * 1024  # 1 MiB
DEFAULT_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}


@dataclass
class IngestSummary:
    scanned: int = 0
    inserted: int = 0
    skipped_duplicate: int = 0
    skipped_unreadable: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def detect_phase_from_path(path: Path) -> ImagePhase | None:
    """Best-effort phase detection from filename / parent directory.

    Returns None if no signal found; the annotator can correct it later.
    """
    haystack = ' '.join([path.name.lower(), *(p.lower() for p in path.parts[-4:])])
    if 'vili' in haystack or 'lugol' in haystack or 'iodine' in haystack:
        return ImagePhase.vili
    if 'via' in haystack or 'aceto' in haystack:
        return ImagePhase.via
    if 'green' in haystack or 'filter' in haystack:
        return ImagePhase.green_filter
    if 'native' in haystack or 'saline' in haystack:
        return ImagePhase.native
    return None


def _read_dimensions(path: Path) -> tuple[int, int] | tuple[None, None]:
    try:
        with PILImage.open(path) as im:
            return im.size  # (width, height)
    except Exception:
        return None, None


def ingest_directory(
    root: Path,
    dataset_source: str,
    extensions: set[str] = DEFAULT_EXTENSIONS,
    dry_run: bool = False,
    skip_macos_meta: bool = True,
) -> IngestSummary:
    """Walk `root` recursively and add every readable image to the Image table."""
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"Ingestion root does not exist: {root}")

    summary = IngestSummary()

    for path in root.rglob('*'):
        if not path.is_file():
            continue
        if skip_macos_meta and path.name.startswith('._'):
            continue
        if path.suffix.lower() not in extensions:
            continue

        summary.scanned += 1

        try:
            digest = sha256_of_file(path)
        except OSError as e:
            summary.skipped_unreadable += 1
            summary.errors.append(f"{path}: {e}")
            continue

        existing = db.session.query(Image.id).filter_by(sha256=digest).first()
        if existing:
            summary.skipped_duplicate += 1
            continue

        width, height = _read_dimensions(path)
        if width is None:
            summary.skipped_unreadable += 1
            summary.errors.append(f"{path}: cannot read image dimensions")
            continue

        image = Image(
            sha256=digest,
            source_path=str(path.resolve()),
            dataset_source=dataset_source,
            image_phase=detect_phase_from_path(path),
            width_px=width,
            height_px=height,
            file_size_bytes=path.stat().st_size,
        )

        if not dry_run:
            db.session.add(image)
            summary.inserted += 1

    if not dry_run:
        db.session.commit()

    return summary


@dataclass
class UploadResult:
    filename: str
    status: str            # 'ingested' | 'duplicate' | 'error'
    image_id: str | None = None
    message: str | None = None


def ingest_upload(
    file_storage,
    dataset_source: str,
    upload_dir: Path,
    extensions: set[str] = DEFAULT_EXTENSIONS,
) -> UploadResult:
    """Ingest one browser-uploaded image (a werkzeug FileStorage).

    Same guarantees as `ingest_directory`: sha256 dedup, dimension read, phase
    guess. Stored content-addressed (`<sha256>.<ext>`) under `upload_dir` so the
    existing source_path/send_file serving keeps working. Does NOT commit -- the
    caller commits once after a batch.
    """
    name = file_storage.filename or 'upload'
    ext = Path(name).suffix.lower()
    if ext not in extensions:
        return UploadResult(name, 'error', message=f"Unsupported file type: {ext or '(none)'}")

    data = file_storage.read()
    if not data:
        return UploadResult(name, 'error', message='Empty file.')

    digest = hashlib.sha256(data).hexdigest()
    existing = db.session.query(Image).filter_by(sha256=digest).first()
    if existing:
        return UploadResult(name, 'duplicate', image_id=existing.id)

    try:
        with PILImage.open(io.BytesIO(data)) as probe:
            probe.verify()                       # integrity check
        with PILImage.open(io.BytesIO(data)) as im:
            width, height = im.size              # re-open: verify() consumes the file
    except Exception:
        return UploadResult(name, 'error', message='Not a readable image.')

    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{digest}{ext}"
    with open(dest, 'wb') as f:
        f.write(data)

    image = Image(
        sha256=digest,
        source_path=str(dest.resolve()),
        dataset_source=dataset_source,
        image_phase=detect_phase_from_path(Path(name)),
        width_px=width,
        height_px=height,
        file_size_bytes=len(data),
    )
    db.session.add(image)
    db.session.flush()  # assign image.id without committing the batch
    return UploadResult(name, 'ingested', image_id=image.id)
