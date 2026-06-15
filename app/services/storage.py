"""Image blob storage: local filesystem (default) or Supabase Storage.

The backend is chosen by ``COLPAI_STORAGE_BACKEND`` (``local`` | ``supabase``).

An Image row's ``source_path`` holds the *storage reference*:
  - local backend  -> an absolute filesystem path (as it has always been)
  - supabase backend -> an object key within the bucket, e.g. ``"<sha256>.jpg"``

Readers go through :func:`open_image`, which transparently handles both kinds of
reference: an absolute path that exists on disk is read locally (this covers
images registered in-place by ``ingest_directory``), otherwise the reference is
treated as a Supabase object key. That dual behaviour lets a deployment migrate
to Supabase without rewriting historical ``source_path`` values.

The Supabase backend talks to the Storage REST API directly over httpx with
HTTP/1.1 and a generous timeout. We deliberately avoid the supabase-py /
storage3 client: its HTTP/2 transport stalls on some networks and ships a short
default read timeout, which surfaced as httpx.ReadTimeout on real uploads.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from urllib.parse import quote

import httpx
from flask import current_app

# Reuse one connection-pooled httpx client across requests.
_http: httpx.Client | None = None

# Uploading a multi-megabyte colposcopy image over a home uplink can take a
# while; keep connect snappy but allow a long read/write window.
_TIMEOUT = httpx.Timeout(connect=15.0, read=180.0, write=180.0, pool=15.0)


class StorageError(RuntimeError):
    """Raised when a storage operation can't be completed (e.g. misconfig)."""


def _backend() -> str:
    return (current_app.config.get('STORAGE_BACKEND') or 'local').strip().lower()


def _client() -> httpx.Client:
    global _http
    if _http is None:
        # HTTP/1.1 (http2=False) avoids the storage3 stall seen on some networks.
        _http = httpx.Client(http2=False, timeout=_TIMEOUT)
    return _http


def _supabase_cfg() -> tuple[str, str, str]:
    cfg = current_app.config
    url = cfg.get('SUPABASE_URL')
    key = cfg.get('SUPABASE_SERVICE_KEY')
    bucket = cfg.get('SUPABASE_BUCKET')
    if not (url and key and bucket):
        raise StorageError(
            'Supabase storage selected but SUPABASE_URL / SUPABASE_SERVICE_KEY / '
            'SUPABASE_BUCKET are not all set.'
        )
    return url.rstrip('/'), key, bucket


def _headers(key: str, extra: dict | None = None) -> dict:
    headers = {'Authorization': f'Bearer {key}', 'apikey': key}
    if extra:
        headers.update(extra)
    return headers


def _object_url(base: str, bucket: str, obj_key: str) -> str:
    # Encode the key but keep path separators so "folder/name.jpg" works.
    return f"{base}/storage/v1/object/{quote(bucket)}/{quote(obj_key, safe='/')}"


def save_image(data: bytes, key: str, content_type: str = 'application/octet-stream') -> str:
    """Persist image ``data`` under ``key`` and return the ``source_path`` ref.

    ``key`` is a bucket-relative object name (e.g. ``"<sha256>.jpg"``). For the
    local backend it becomes a file under ``UPLOAD_DIR``; for supabase it's the
    object key uploaded to the bucket.
    """
    if _backend() == 'supabase':
        base, svc_key, bucket = _supabase_cfg()
        # x-upsert keeps re-ingesting the same sha256 idempotent.
        headers = _headers(svc_key, {'Content-Type': content_type, 'x-upsert': 'true'})
        try:
            resp = _client().post(_object_url(base, bucket, key), content=data, headers=headers)
        except httpx.HTTPError as e:
            raise StorageError(f"Upload to Supabase failed: {e}") from e
        if resp.status_code not in (200, 201):
            raise StorageError(
                f"Upload to Supabase returned {resp.status_code}: {resp.text[:300]}"
            )
        return key

    upload_dir = Path(current_app.config['UPLOAD_DIR'])
    dest = upload_dir / key
    # key may contain subdirectories (e.g. "crops/<id>.png"), so make the full
    # parent path, not just UPLOAD_DIR.
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return str(dest.resolve())


def move_image(src_key: str, dst_key: str) -> str:
    """Rename a stored object from ``src_key`` to ``dst_key``; return ``dst_key``.

    No-op when the keys are equal. For supabase this uses the Storage move API
    (no re-upload); for local it renames the file under ``UPLOAD_DIR``.
    """
    if src_key == dst_key:
        return dst_key

    if _backend() == 'supabase':
        base, svc_key, bucket = _supabase_cfg()
        body = {'bucketId': bucket, 'sourceKey': src_key, 'destinationKey': dst_key}
        try:
            resp = _client().post(
                f"{base}/storage/v1/object/move",
                json=body,
                headers=_headers(svc_key, {'Content-Type': 'application/json'}),
            )
        except httpx.HTTPError as e:
            raise StorageError(f"Move on Supabase failed: {e}") from e
        if resp.status_code not in (200, 201):
            raise StorageError(
                f"Move on Supabase returned {resp.status_code}: {resp.text[:300]}"
            )
        return dst_key

    upload_dir = Path(current_app.config['UPLOAD_DIR'])
    src = upload_dir / src_key
    dest = upload_dir / dst_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dest)
    return str(dest.resolve())


def delete_image(source_path: str) -> bool:
    """Delete a stored object. Returns True if removed, False if it wasn't there.

    Never raises for a missing object -- a delete that finds nothing already
    achieved its goal. Other failures raise :class:`StorageError`.
    """
    # On-disk file (local backend, or in-place ingested images).
    if os.path.isabs(source_path) and os.path.exists(source_path):
        try:
            os.remove(source_path)
            return True
        except OSError as e:
            raise StorageError(f"Could not delete '{source_path}': {e}") from e

    if _backend() == 'supabase':
        base, svc_key, bucket = _supabase_cfg()
        try:
            resp = _client().delete(_object_url(base, bucket, source_path), headers=_headers(svc_key))
        except httpx.HTTPError as e:
            raise StorageError(f"Delete on Supabase failed: {e}") from e
        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 404:
            return False
        raise StorageError(
            f"Delete on Supabase returned {resp.status_code}: {resp.text[:300]}"
        )

    return False


def open_image(source_path: str) -> io.IOBase:
    """Return a binary, file-like handle for an Image's ``source_path``.

    Raises ``FileNotFoundError`` (local) or :class:`StorageError` (supabase) if
    the blob can't be read, so callers can degrade gracefully.
    """
    # A real on-disk file always wins, regardless of backend. This keeps images
    # registered in-place by ingest_directory working after a switch to supabase.
    if os.path.isabs(source_path) and os.path.exists(source_path):
        return open(source_path, 'rb')

    if _backend() == 'supabase':
        base, svc_key, bucket = _supabase_cfg()
        try:
            resp = _client().get(_object_url(base, bucket, source_path), headers=_headers(svc_key))
        except httpx.HTTPError as e:
            raise StorageError(f"Could not download '{source_path}' from Supabase: {e}") from e
        if resp.status_code != 200:
            raise StorageError(
                f"Could not download '{source_path}' from Supabase "
                f"(status {resp.status_code})."
            )
        return io.BytesIO(resp.content)

    raise FileNotFoundError(source_path)


def image_exists(source_path: str) -> bool:
    """Cheap-ish existence check used before serving a file."""
    if os.path.isabs(source_path) and os.path.exists(source_path):
        return True
    if _backend() == 'supabase':
        base, svc_key, bucket = _supabase_cfg()
        try:
            resp = _client().head(_object_url(base, bucket, source_path), headers=_headers(svc_key))
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
    return False
