"""Analytics endpoints: dashboard summary + usage/content/quality tabs (FR-17)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.db.models import User
from app.schemas.analytics import ContentOut, DashboardOut, QualityOut, UsageOut
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=DashboardOut)
def summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DashboardOut:
    return analytics_service.dashboard(db, user)


@router.get("/usage", response_model=UsageOut)
def usage(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UsageOut:
    return analytics_service.usage(db, user.id)


@router.get("/content", response_model=ContentOut)
def content(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ContentOut:
    return analytics_service.content(db, user.id)


@router.get("/quality", response_model=QualityOut)
def quality(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> QualityOut:
    return analytics_service.quality(db, user.id)
