"""Patient-level FINAL diagnosis API + consensus tests.

NB on the fixture: unlike test_admin.py / test_exporters.py (which only ever
authenticate one user per test), several tests here need two *simultaneously*
logged-in test clients (two annotators submitting diagnoses for the same
patient). Flask's `g` -- and therefore `flask_login.current_user` -- lives on
the *application context*, not the request context; if the app context stays
manually pushed for the whole test (the pattern those other files use), Flask's
test client reuses it instead of pushing a fresh one per request, so `g` (and
"who's logged in") leaks across clients. Popping the context before issuing any
requests lets the test client push its own per-request context, which keeps
`current_user` correctly scoped to whichever client made the call.
"""
from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db
from app.models import Image, ImageAnnotation, PatientConsensus, PatientDiagnosis, User
from app.models.enums import UserRole


@pytest.fixture()
def app(tmp_path):
    app = create_app('test')
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    _seed()
    ctx.pop()

    yield app

    ctx.push()
    db.session.remove()
    db.drop_all()
    ctx.pop()


def _seed():
    alice = User(username='alice', role=UserRole.annotator)
    alice.set_password('pw')
    bob = User(username='bob', role=UserRole.annotator)
    bob.set_password('pw')
    reviewer = User(username='reviewer1', role=UserRole.reviewer)
    reviewer.set_password('pw')
    db.session.add_all([alice, bob, reviewer])

    db.session.add_all([
        Image(sha256='a' * 64, source_path='a.jpg', dataset_source='ds', patient_code='PAT-001'),
        Image(sha256='b' * 64, source_path='b.jpg', dataset_source='ds', patient_code='PAT-001'),
        Image(sha256='c' * 64, source_path='c.jpg', dataset_source='ds', patient_code=None),
    ])
    db.session.commit()


def _login(client, username):
    return client.post('/api/v1/auth/login', json={'username': username, 'password': 'pw'})


def test_create_or_get_diagnosis_idempotent(app):
    c = app.test_client()
    _login(c, 'alice')
    r1 = c.post('/api/v1/patients/PAT-001/diagnosis')
    assert r1.status_code == 201
    id1 = r1.get_json()['id']

    r2 = c.post('/api/v1/patients/PAT-001/diagnosis')
    assert r2.status_code == 200
    assert r2.get_json()['id'] == id1
    with app.app_context():
        assert db.session.query(PatientDiagnosis).count() == 1


def test_unknown_or_codeless_patient_404(app):
    c = app.test_client()
    _login(c, 'alice')
    assert c.post('/api/v1/patients/NOPE/diagnosis').status_code == 404
    assert c.get('/api/v1/patients/NOPE').status_code == 404


def test_patch_persists_and_blocks_after_submit(app):
    c = app.test_client()
    _login(c, 'alice')
    c.post('/api/v1/patients/PAT-001/diagnosis')

    r = c.patch('/api/v1/patients/PAT-001/diagnosis', json={
        'colposcopic_impression': ['CIN1'], 'confidence': 3,
    })
    assert r.status_code == 200

    sub = c.post('/api/v1/patients/PAT-001/diagnosis/submit')
    assert sub.status_code == 200
    assert sub.get_json()['diagnosis']['status'] == 'submitted'

    blocked = c.patch('/api/v1/patients/PAT-001/diagnosis', json={'notes': 'late edit'})
    assert blocked.status_code == 409

    resubmit = c.post('/api/v1/patients/PAT-001/diagnosis/submit')
    assert resubmit.status_code == 409


def test_submit_requires_impression_and_confidence(app):
    c = app.test_client()
    _login(c, 'alice')
    c.post('/api/v1/patients/PAT-001/diagnosis')

    r = c.post('/api/v1/patients/PAT-001/diagnosis/submit')
    assert r.status_code == 422

    r2 = c.post('/api/v1/patients/PAT-001/diagnosis/submit', json={'colposcopic_impression': ['NORMAL']})
    assert r2.status_code == 422


def test_matching_diagnoses_produce_consensus(app):
    c1, c2 = app.test_client(), app.test_client()
    _login(c1, 'alice')
    _login(c2, 'bob')

    for c in (c1, c2):
        c.post('/api/v1/patients/PAT-001/diagnosis')
        r = c.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
            'colposcopic_impression': ['CIN2'], 'confidence': 4,
        })
        assert r.status_code == 200

    with app.app_context():
        consensus = db.session.query(PatientConsensus).filter_by(patient_code='PAT-001').one()
        assert consensus.label == ['CIN2']
        assert consensus.agreement_score == 1.0

    detail = c1.get('/api/v1/patients/PAT-001').get_json()
    assert detail['consensus']['label'] == ['CIN2']
    assert detail['submitted_diagnosis_count'] == 2


def test_disagreeing_diagnoses_show_in_review_disagreements(app):
    c1, c2, r = app.test_client(), app.test_client(), app.test_client()
    _login(c1, 'alice')
    _login(c2, 'bob')
    _login(r, 'reviewer1')

    c1.post('/api/v1/patients/PAT-001/diagnosis')
    c1.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
        'colposcopic_impression': ['CIN2'], 'confidence': 4,
    })
    c2.post('/api/v1/patients/PAT-001/diagnosis')
    c2.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
        'colposcopic_impression': ['NORMAL'], 'confidence': 5,
    })

    # Consensus still picks a winner via confidence tie-break (matching the
    # pre-existing per-image logic), but agreement is only 50%.
    with app.app_context():
        consensus = db.session.query(PatientConsensus).filter_by(patient_code='PAT-001').one()
        assert consensus.agreement_score == 0.5

    items = r.get('/api/v1/review/disagreements').get_json()['items']
    assert any(i['patient_code'] == 'PAT-001' for i in items)


def test_get_patient_detail_shape(app):
    c = app.test_client()
    _login(c, 'alice')
    r = c.get('/api/v1/patients/PAT-001')
    assert r.status_code == 200
    body = r.get_json()
    assert body['patient_code'] == 'PAT-001'
    assert len(body['images']) == 2
    assert body['my_diagnosis'] is None
    assert body['consensus'] is None


def test_dashboard_reflects_patient_diagnoses(app):
    c = app.test_client()
    _login(c, 'alice')
    c.post('/api/v1/patients/PAT-001/diagnosis')
    c.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
        'colposcopic_impression': ['CIN1'], 'confidence': 3,
    })

    dist = c.get('/api/v1/dashboard/distribution').get_json()
    assert {'label': 'CIN1', 'count': 1} in dist['items']

    agree = c.get('/api/v1/dashboard/agreement').get_json()
    assert 'multi_rater_patients' in agree


def test_per_image_submit_no_longer_requires_diagnosis(app):
    c = app.test_client()
    _login(c, 'alice')
    with app.app_context():
        image_id = db.session.query(Image).filter_by(patient_code='PAT-001').first().id

    created = c.post('/api/v1/annotations', json={'image_id': image_id})
    assert created.status_code == 201
    ann_id = created.get_json()['id']

    r = c.post(f'/api/v1/annotations/{ann_id}/submit')
    assert r.status_code == 200
    assert r.get_json()['status'] == 'submitted'


def test_diagnosis_submit_finalizes_image_drafts(app):
    """There's no per-image submit anymore -- submitting the patient's diagnosis
    finalizes (draft -> submitted) whatever image drafts this annotator has for
    that patient. Images never opened are left alone."""
    c = app.test_client()
    _login(c, 'alice')
    with app.app_context():
        image_ids = [img.id for img in db.session.query(Image).filter_by(patient_code='PAT-001').all()]

    # Draft one image, leave the other untouched.
    r = c.post('/api/v1/annotations', json={'image_id': image_ids[0]})
    ann_id = r.get_json()['id']
    c.patch(f'/api/v1/annotations/{ann_id}', json={'notes': 'wip'})

    with app.app_context():
        assert db.session.get(ImageAnnotation, ann_id).status.value == 'draft'

    c.post('/api/v1/patients/PAT-001/diagnosis')
    r = c.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
        'colposcopic_impression': ['CIN1'], 'confidence': 3,
    })
    assert r.status_code == 200
    assert r.get_json()['images_finalized'] == 1

    with app.app_context():
        ann = db.session.get(ImageAnnotation, ann_id)
        assert ann.status.value == 'submitted'
        assert ann.submitted_at is not None

    detail = c.get('/api/v1/patients/PAT-001').get_json()
    statuses = {img['id']: img['my_annotation_status'] for img in detail['images']}
    assert statuses[image_ids[0]] == 'submitted'
    assert statuses[image_ids[1]] is None  # never opened -- left alone


def test_reid_swede_scoring_on_patient_diagnosis(app):
    c = app.test_client()
    _login(c, 'alice')
    c.post('/api/v1/patients/PAT-001/diagnosis')
    c.patch('/api/v1/patients/PAT-001/diagnosis', json={
        'reid_margin': 2, 'reid_color': 2, 'reid_vessels': 2, 'reid_iodine': 2,
        'swede_aceto': 1, 'swede_margin': 1, 'swede_vessels': 1, 'swede_size': 1, 'swede_iodine': 1,
    })

    r = c.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
        'colposcopic_impression': ['CIN3'], 'confidence': 5,
    })
    assert r.status_code == 200
    diagnosis = r.get_json()['diagnosis']
    assert diagnosis['reid_total'] == 8
    assert diagnosis['swede_total'] == 5

    # Reid/Swede are never required to submit -- a bare impression+confidence suffices.
    with app.app_context():
        db.session.query(PatientDiagnosis).filter_by(patient_code='PAT-001').delete()
        db.session.commit()
    c.post('/api/v1/patients/PAT-001/diagnosis')
    bare = c.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
        'colposcopic_impression': ['NORMAL'], 'confidence': 3,
    })
    assert bare.status_code == 200
    assert bare.get_json()['diagnosis']['reid_total'] is None


def test_reject_reopens_patient_and_flags_image(app):
    """A reviewer rejecting one image supersedes it, drops the annotator's patient
    diagnosis back to draft, and the image is flagged 'needs_redo' with the comment;
    re-submitting the patient re-finalizes the redone image into the review queue."""
    doc, rev = app.test_client(), app.test_client()
    _login(doc, 'alice')
    _login(rev, 'reviewer1')

    with app.app_context():
        image_id = db.session.query(Image).filter_by(patient_code='PAT-001').first().id

    # Annotator drafts an image, then submits the patient diagnosis (bulk-finalizes it).
    ann_id = doc.post('/api/v1/annotations', json={'image_id': image_id}).get_json()['id']
    doc.patch(f'/api/v1/annotations/{ann_id}', json={'notes': 'v1'})
    doc.post('/api/v1/patients/PAT-001/diagnosis')
    doc.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
        'colposcopic_impression': ['CIN1'], 'confidence': 3,
    })
    with app.app_context():
        assert db.session.get(ImageAnnotation, ann_id).status.value == 'submitted'

    # Reviewer rejects it -> reopens the patient.
    r = rev.post(f'/api/v1/review/{ann_id}/reject', json={'comment': 'blurry, redo'})
    assert r.status_code == 200
    assert r.get_json()['reopened_patient'] == 'PAT-001'

    with app.app_context():
        assert db.session.get(ImageAnnotation, ann_id).status.value == 'superseded'
        pd = db.session.query(PatientDiagnosis).filter_by(patient_code='PAT-001').one()
        assert pd.status.value == 'draft'
        assert pd.submitted_at is None

    # The patient detail flags the image for redo, with the reviewer's comment.
    detail = doc.get('/api/v1/patients/PAT-001').get_json()
    flagged = [i for i in detail['images'] if i['id'] == image_id][0]
    assert flagged['needs_redo'] is True
    assert flagged['reviewer_rejection'] == 'blurry, redo'

    # Annotator redraws (new version) and re-submits the patient -> back in the queue.
    v2 = doc.post('/api/v1/annotations', json={'image_id': image_id}).get_json()['id']
    assert v2 != ann_id
    doc.patch(f'/api/v1/annotations/{v2}', json={'notes': 'v2 fixed'})
    doc.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
        'colposcopic_impression': ['CIN1'], 'confidence': 3,
    })
    with app.app_context():
        assert db.session.get(ImageAnnotation, v2).status.value == 'submitted'

    # The redone (v2) annotation appears in the reviewer queue; the flag clears.
    queue_ids = [i['id'] for i in rev.get('/api/v1/review/queue').get_json()['items']]
    assert v2 in queue_ids
    detail2 = doc.get('/api/v1/patients/PAT-001').get_json()
    flagged2 = [i for i in detail2['images'] if i['id'] == image_id][0]
    assert flagged2['needs_redo'] is False


def test_screening_context_fields_persist(app):
    """Cytology, HPV, management, and biopsy-taken autosave and round-trip."""
    c = app.test_client()
    _login(c, 'alice')
    c.post('/api/v1/patients/PAT-001/diagnosis')
    r = c.patch('/api/v1/patients/PAT-001/diagnosis', json={
        'cytology_result': 'HSIL',
        'hpv_status': 'positive_16_18',
        'management_recommendation': 'excision_leep',
        'biopsy_taken': True,
    })
    assert r.status_code == 200

    detail = c.get('/api/v1/patients/PAT-001').get_json()['my_diagnosis']
    assert detail['cytology_result'] == 'HSIL'
    assert detail['hpv_status'] == 'positive_16_18'
    assert detail['management_recommendation'] == 'excision_leep'
    assert detail['biopsy_taken'] is True

    # Bad enum value is rejected.
    bad = c.patch('/api/v1/patients/PAT-001/diagnosis', json={'cytology_result': 'NOT_A_CODE'})
    assert bad.status_code == 422


def test_image_ifcpc_assessment_persists(app):
    """Per-image IFCPC grade + adequacy autosave through the annotation block."""
    c = app.test_client()
    _login(c, 'alice')
    with app.app_context():
        image_id = db.session.query(Image).filter_by(patient_code='PAT-001').first().id

    created = c.post('/api/v1/annotations', json={'image_id': image_id})
    ann_id = created.get_json()['id']
    r = c.patch(f'/api/v1/annotations/{ann_id}', json={
        'assessment': {'ifcpc_grade': 'major', 'colposcopy_adequacy': 'adequate'},
    })
    assert r.status_code == 200

    got = c.get(f'/api/v1/annotations/{ann_id}').get_json()
    assert got['assessment']['ifcpc_grade'] == 'major'
    assert got['assessment']['colposcopy_adequacy'] == 'adequate'


def test_image_phase_patch(app):
    c = app.test_client()
    _login(c, 'alice')
    with app.app_context():
        image_id = db.session.query(Image).filter_by(patient_code='PAT-001').first().id

    r = c.patch(f'/api/v1/images/{image_id}', json={'image_phase': 'vili'})
    assert r.status_code == 200
    assert r.get_json()['image_phase'] == 'vili'

    with app.app_context():
        assert db.session.get(Image, image_id).image_phase.value == 'vili'

    bad = c.patch(f'/api/v1/images/{image_id}', json={'image_phase': 'not_a_phase'})
    assert bad.status_code == 422


def test_export_patients_csv(app):
    c = app.test_client()
    _login(c, 'alice')
    c.post('/api/v1/patients/PAT-001/diagnosis')
    c.post('/api/v1/patients/PAT-001/diagnosis/submit', json={
        'colposcopic_impression': ['CIN3'], 'confidence': 5,
    })

    r = app.test_client()
    _login(r, 'reviewer1')
    resp = r.get('/api/v1/export/patients')
    assert resp.status_code == 200
    assert resp.mimetype == 'text/csv'
    text = resp.get_data(as_text=True)
    assert 'patient_code' in text.splitlines()[0]
    assert 'PAT-001' in text and 'CIN3' in text
