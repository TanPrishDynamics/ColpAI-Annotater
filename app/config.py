"""Configuration for the ColpAI annotation platform."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)

_DEFAULT_DB_URI = f"sqlite:///{DATA_DIR / 'annotations.db'}"


def _engine_options(uri: str) -> dict:
    """Engine kwargs appropriate to the DB driver.

    ``check_same_thread`` is SQLite-only and crashes psycopg, so it must never
    be sent to a Postgres connection (e.g. Supabase). Postgres instead gets a
    recycle window so the connection pool survives Supabase's idle timeouts.
    """
    if uri.startswith('sqlite'):
        return {'connect_args': {'check_same_thread': False}, 'pool_pre_ping': True}
    return {'pool_pre_ping': True, 'pool_recycle': 1800}


class BaseConfig:
    SECRET_KEY = os.environ.get('COLPAI_SECRET_KEY', 'dev-secret-change-me-in-prod')

    # Point this at the Supabase Postgres connection string to use Supabase as
    # the backend, e.g.
    #   postgresql+psycopg://postgres.<ref>:<pw>@<host>:5432/postgres
    SQLALCHEMY_DATABASE_URI = os.environ.get('COLPAI_DATABASE_URI', _DEFAULT_DB_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options(SQLALCHEMY_DATABASE_URI)

    JSON_SORT_KEYS = False
    # Whole-request cap. Bulk image upload sends many files at once, so this is
    # generous; tune with COLPAI_MAX_UPLOAD_MB.
    MAX_CONTENT_LENGTH = int(os.environ.get('COLPAI_MAX_UPLOAD_MB', 200)) * 1024 * 1024

    # --- Blob storage: where uploaded image files live ---
    # 'local' (default) stores under UPLOAD_DIR; 'supabase' uses Supabase Storage.
    STORAGE_BACKEND = os.environ.get('COLPAI_STORAGE_BACKEND', 'local').strip().lower()
    UPLOAD_DIR = os.environ.get('COLPAI_UPLOAD_DIR', str(DATA_DIR / 'uploads'))

    # Supabase project credentials (only required when STORAGE_BACKEND=supabase).
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    # Server-side key. Use the SERVICE ROLE key so the backend can read/write the
    # bucket regardless of RLS; never expose it to the browser.
    SUPABASE_SERVICE_KEY = os.environ.get('SUPABASE_SERVICE_KEY')
    SUPABASE_BUCKET = os.environ.get('SUPABASE_BUCKET', 'colpai-images')

    IMAGE_QUEUE_PAGE_SIZE = 50
    IMAGE_INGEST_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}


class DevConfig(BaseConfig):
    DEBUG = True


class TestConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options('sqlite:///:memory:')
    # Tests always exercise the local storage backend, never a real bucket.
    STORAGE_BACKEND = 'local'
    WTF_CSRF_ENABLED = False


class ProdConfig(BaseConfig):
    DEBUG = False

    # SECRET_KEY has no dev fallback here; create_app() refuses to boot prod
    # without a strong COLPAI_SECRET_KEY (a forgeable key = forgeable logins).
    SECRET_KEY = os.environ.get('COLPAI_SECRET_KEY')

    # Cookies only travel over HTTPS, aren't readable by JS, aren't sent
    # cross-site. Essential once patient images are served over the internet.
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True


CONFIG_BY_NAME = {
    'dev': DevConfig,
    'test': TestConfig,
    'prod': ProdConfig,
}
