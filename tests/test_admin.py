"""Admin user-management + image-upload API tests."""
from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage

from app import create_app
from app.extensions import db
from app.models import Image, User
from app.models.enums import UserRole


@pytest.fixture()
def app(tmp_path):
    app = create_app('test')
    app.config['UPLOAD_DIR'] = str(tmp_path / 'uploads')
    with app.app_context():
        db.create_all()
        admin = User(username='admin1', role=UserRole.admin)
        admin.set_password('pw')
        annot = User(username='alice', role=UserRole.annotator)
        annot.set_password('pw')
        db.session.add_all([admin, annot])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def _png_upload(color=(120, 30, 30), size=(64, 48), name='scan.png'):
    bio = io.BytesIO()
    PILImage.new('RGB', size, color).save(bio, format='PNG')
    bio.seek(0)
    return (bio, name)


def _login(client, username):
    return client.post('/api/v1/auth/login', json={'username': username, 'password': 'pw'})


def test_non_admin_blocked(app):
    c = app.test_client()
    _login(c, 'alice')
    assert c.get('/api/v1/admin/users').status_code == 403
    assert c.post('/api/v1/admin/users', json={}).status_code == 403


def test_admin_lists_users_with_progress(app):
    c = app.test_client()
    _login(c, 'admin1')
    r = c.get('/api/v1/admin/users')
    assert r.status_code == 200
    items = r.get_json()['items']
    assert {u['username'] for u in items} == {'admin1', 'alice'}
    assert all('progress' in u for u in items)


def test_create_user_and_login(app):
    c = app.test_client()
    _login(c, 'admin1')
    r = c.post('/api/v1/admin/users', json={
        'username': 'dr_smith', 'password': 'secret1', 'role': 'annotator', 'full_name': 'Dr Smith',
    })
    assert r.status_code == 201
    assert r.get_json()['role'] == 'annotator'

    # The new doctor can log in immediately.
    c2 = app.test_client()
    assert c2.post('/api/v1/auth/login',
                   json={'username': 'dr_smith', 'password': 'secret1'}).status_code == 200


def test_duplicate_username_rejected(app):
    c = app.test_client()
    _login(c, 'admin1')
    body = {'username': 'alice', 'password': 'secret1', 'role': 'annotator'}
    assert c.post('/api/v1/admin/users', json=body).status_code == 409


def test_disable_blocks_login(app):
    c = app.test_client()
    _login(c, 'admin1')
    alice = db.session.query(User).filter_by(username='alice').first()
    r = c.patch(f'/api/v1/admin/users/{alice.id}', json={'is_active': False})
    assert r.status_code == 200
    assert r.get_json()['is_active'] is False

    blocked = app.test_client().post('/api/v1/auth/login',
                                     json={'username': 'alice', 'password': 'pw'})
    assert blocked.status_code == 403


def test_admin_cannot_disable_self(app):
    c = app.test_client()
    _login(c, 'admin1')
    me = db.session.query(User).filter_by(username='admin1').first()
    assert c.patch(f'/api/v1/admin/users/{me.id}', json={'is_active': False}).status_code == 409


# --- image upload ---

def test_upload_requires_admin(app):
    c = app.test_client()
    _login(c, 'alice')
    r = c.post('/api/v1/admin/images/upload',
               data={'dataset': 'd', 'files': _png_upload()},
               content_type='multipart/form-data')
    assert r.status_code == 403


def test_upload_creates_images_and_dedupes(app):
    c = app.test_client()
    _login(c, 'admin1')

    # Two distinct images + one exact duplicate of the first.
    r = c.post('/api/v1/admin/images/upload', content_type='multipart/form-data', data={
        'dataset': 'clinic_june',
        'files': [
            _png_upload((10, 20, 30), name='a.png'),
            _png_upload((90, 80, 70), name='b.png'),
            _png_upload((10, 20, 30), name='a_again.png'),  # same pixels as a.png
        ],
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body['counts']['ingested'] == 2
    assert body['counts']['duplicate'] == 1
    assert body['dataset'] == 'clinic_june'

    images = db.session.query(Image).all()
    assert len(images) == 2
    assert all(img.dataset_source == 'clinic_june' for img in images)
    assert all(img.width_px == 64 and img.height_px == 48 for img in images)

    # Re-uploading an already-stored image is also a duplicate.
    r2 = c.post('/api/v1/admin/images/upload', content_type='multipart/form-data',
                data={'dataset': 'clinic_june', 'files': _png_upload((10, 20, 30), name='a.png')})
    assert r2.get_json()['counts']['duplicate'] == 1
    assert db.session.query(Image).count() == 2


def test_upload_rejects_non_image_and_missing_dataset(app):
    c = app.test_client()
    _login(c, 'admin1')

    # Missing dataset.
    assert c.post('/api/v1/admin/images/upload', content_type='multipart/form-data',
                  data={'files': _png_upload()}).status_code == 422

    # A .txt masquerading as an upload -> per-file error, none ingested.
    bad = (io.BytesIO(b'not an image'), 'notes.txt')
    r = c.post('/api/v1/admin/images/upload', content_type='multipart/form-data',
               data={'dataset': 'd', 'files': bad})
    assert r.status_code == 201
    assert r.get_json()['counts']['ingested'] == 0
    assert r.get_json()['counts']['error'] == 1
