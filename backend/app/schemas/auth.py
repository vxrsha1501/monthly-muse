"""Auth and profile schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str | None = Field(default=None, max_length=120)
    timezone: str = "Asia/Kolkata"
    region: str = "India"
    locale: str = "en"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: EmailStr
    full_name: str | None
    timezone: str
    region: str
    locale: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MeUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    timezone: str | None = None
    region: str | None = None
    locale: str | None = None
