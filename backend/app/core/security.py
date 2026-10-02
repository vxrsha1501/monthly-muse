"""Authentication primitives: Argon2id password hashing + JWT access/refresh tokens."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from app.core.config import settings
from app.core.errors import Unauthorized

_hasher = PasswordHasher(time_cost=2, memory_cost=64 * 1024, parallelism=2)

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, role: str = "user") -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "iat": _now(),
        "exp": _now() + timedelta(minutes=settings.access_token_ttl_minutes),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": _now(),
        "exp": _now() + timedelta(days=settings.refresh_token_ttl_days),
        "jti": secrets.token_hex(16),  # rotation: every refresh issues a new jti
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_ics_token(user_id: str) -> str:
    """Long-lived token for the calendar feed subscription (GET /calendar.ics)."""
    payload = {
        "sub": user_id,
        "type": "ics",
        "iat": _now(),
        "exp": _now() + timedelta(days=365),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str, expected_type: str = "access") -> dict:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("Invalid token") from exc
    if payload.get("type") != expected_type:
        raise Unauthorized("Wrong token type")
    return payload
