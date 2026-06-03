"""Configuration for the ColpAI annotation platform."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(exist_ok=True)


class BaseConfig:
    SECRET_KEY = os.environ.get('COLPAI_SECRET_KEY', 'dev-secret-change-me-in-prod')

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'COLPAI_DATABASE_URI',
        f"sqlite:///{DATA_DIR / 'annotations.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'connect_args': {'check_same_thread': False},
        'pool_pre_ping': True,
    }

    JSON_SORT_KEYS = False
    # Whole-request cap. Bulk image upload sends many files at once, so this is
    # generous; tune with COLPAI_MAX_UPLOAD_MB.
    MAX_CONTENT_LENGTH = int(os.environ.get('COLPAI_MAX_UPLOAD_MB', 200)) * 1024 * 1024

    # Where browser-uploaded images are stored (served later via source_path).
    UPLOAD_DIR = os.environ.get('COLPAI_UPLOAD_DIR', str(DATA_DIR / 'uploads'))

    IMAGE_QUEUE_PAGE_SIZE = 50
    IMAGE_INGEST_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}


class DevConfig(BaseConfig):
    DEBUG = True


class TestConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
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
