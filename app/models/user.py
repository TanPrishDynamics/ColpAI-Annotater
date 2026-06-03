"""User model. Annotators, reviewers, and admins all live in one table, distinguished by role."""
from datetime import datetime, timezone
import uuid

from flask_login import UserMixin
from sqlalchemy import Enum as SAEnum
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db, login_manager
from app.models.enums import UserRole


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(SAEnum(UserRole, name='user_role'), nullable=False, default=UserRole.annotator)
    full_name = db.Column(db.String(128), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)
    is_active_flag = db.Column('is_active', db.Boolean, default=True, nullable=False)

    annotations = db.relationship('ImageAnnotation', back_populates='annotator', lazy='dynamic')

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    @property
    def is_active(self) -> bool:
        return self.is_active_flag

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'username': self.username,
            'role': self.role.value,
            'full_name': self.full_name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login': self.last_login.isoformat() if self.last_login else None,
        }

    def __repr__(self) -> str:
        return f"<User {self.username} role={self.role.value}>"


@login_manager.user_loader
def _load_user(user_id: str):
    return db.session.get(User, user_id)
