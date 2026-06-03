"""Pydantic schemas for auth endpoints."""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)


class UserOut(BaseModel):
    id: str
    username: str
    role: str
    full_name: str | None = None
