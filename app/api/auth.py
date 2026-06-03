"""Auth blueprint. Username + password login backed by Flask-Login session cookies."""
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user

from app.api.errors import error_response
from app.extensions import db
from app.models import User
from app.schemas.auth import LoginRequest, UserOut

bp = Blueprint('auth', __name__, url_prefix='/api/v1/auth')


@bp.post('/login')
def login():
    payload = LoginRequest.model_validate(request.get_json(silent=True) or {})

    user = db.session.query(User).filter_by(username=payload.username).first()
    if user is None or not user.check_password(payload.password):
        return error_response('invalid_credentials', 'Username or password is incorrect.', status=401)
    if not user.is_active:
        return error_response('inactive_user', 'This account is disabled.', status=403)

    login_user(user, remember=True)
    user.last_login = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify(UserOut(
        id=user.id,
        username=user.username,
        role=user.role.value,
        full_name=user.full_name,
    ).model_dump())


@bp.post('/logout')
@login_required
def logout():
    logout_user()
    return jsonify({'status': 'ok'})


@bp.get('/me')
@login_required
def me():
    return jsonify(UserOut(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role.value,
        full_name=current_user.full_name,
    ).model_dump())
