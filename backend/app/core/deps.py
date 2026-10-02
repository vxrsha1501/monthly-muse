"""Shared FastAPI dependencies: DB session, current user, admin gate."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import Forbidden, Unauthorized
from app.core.rate_limit import rate_limit
from app.core.security import decode_token
from app.db.models import User
from app.db.session import SessionLocal

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Iterator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db=Depends(get_db),
    x_user: str | None = Header(default=None, alias="X-User-Id"),
) -> User:
    """Bearer JWT, or X-User-Id for the lightweight local demo flow."""
    user_id: str | None = None
    if credentials is not None:
        payload = decode_token(credentials.credentials, expected_type="access")
        user_id = payload.get("sub")
    elif x_user:
        user_id = x_user
    if not user_id:
        raise Unauthorized()
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Account not found or disabled")
    rate_limit(str(user.id), "api")
    request.state.user_id = str(user.id)
    return user


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise Forbidden("Admin only")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
