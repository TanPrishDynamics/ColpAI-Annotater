"""Pydantic schemas for admin user-management endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import UserRole


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: UserRole = UserRole.annotator
    full_name: str | None = Field(default=None, max_length=128)


class UserUpdate(BaseModel):
    """All optional: change role, rename, enable/disable, or reset password."""
    role: UserRole | None = None
    full_name: str | None = Field(default=None, max_length=128)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=6, max_length=128)
