"""Auth endpoints: register, login, rotating refresh cookie, logout (Section 10.2)."""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut
from app.services import account_service

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "mm_refresh"


def _set_refresh(response: Response, user_id: str) -> None:
    token = create_refresh_token(user_id)
    # Local dev serves frontend and API on the same site (localhost:3000 ->
    # localhost:8000), so Lax works. In production the blueprint deploys them on
    # different domains (Vercel + API host), which requires None+Secure (HTTPS).
    production = settings.environment == "production"
    response.set_cookie(
        key=REFRESH_COOKIE, value=token, httponly=True,
        samesite="none" if production else "lax",
        secure=production,
        max_age=settings.refresh_token_ttl_days * 86400, path="/",
    )


@router.post("/register", response_model=TokenOut, status_code=201)
def register(data: RegisterIn, response: Response, db: Session = Depends(get_db)) -> TokenOut:
    user = account_service.register(db, data)
    _set_refresh(response, user.id)
    return TokenOut(access_token=create_access_token(user.id, user.role), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenOut)
def login(data: LoginIn, response: Response, db: Session = Depends(get_db)) -> TokenOut:
    user = account_service.authenticate(db, data.email, data.password)
    _set_refresh(response, user.id)
    return TokenOut(access_token=create_access_token(user.id, user.role), user=UserOut.model_validate(user))


@router.post("/refresh", response_model=TokenOut)
def refresh(response: Response, db: Session = Depends(get_db),
            mm_refresh: str | None = Cookie(default=None, alias=REFRESH_COOKIE)) -> TokenOut:
    """Rotating refresh token: every refresh issues a new cookie."""
    payload = decode_token(mm_refresh or "", expected_type="refresh")
    from app.db.models import User
    user = db.get(User, payload.get("sub", ""))
    if user is None or not user.is_active:
        from app.core.errors import Unauthorized
        raise Unauthorized("Account not found or disabled")
    _set_refresh(response, user.id)
    return TokenOut(access_token=create_access_token(user.id, user.role), user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
def logout(response: Response) -> Response:
    response.delete_cookie(REFRESH_COOKIE, path="/")
    response.status_code = 204
    return response
