"""Audience profiles, topics and occasion suggestions (FR-02, FR-04)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.db.models import User
from app.schemas.content import AudienceIn, AudienceOut, OccasionOut, TopicIn, TopicOut
from app.services import catalog_service

router = APIRouter(tags=["catalog"])


@router.get("/audiences", response_model=list[AudienceOut])
def list_audiences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [AudienceOut.model_validate(a) for a in catalog_service.list_audiences(db, user.id)]


@router.post("/audiences", response_model=AudienceOut, status_code=201)
def create_audience(data: AudienceIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    return AudienceOut.model_validate(
        catalog_service.create_audience(db, user.id, data.name, data.description))


@router.patch("/audiences/{row_id}", response_model=AudienceOut)
def patch_audience(row_id: str, data: AudienceIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    return AudienceOut.model_validate(
        catalog_service.update_audience(db, user.id, row_id, data.name, data.description))


@router.delete("/audiences/{row_id}", status_code=204)
def delete_audience(row_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    catalog_service.delete_audience(db, user.id, row_id)


@router.get("/topics", response_model=list[TopicOut])
def list_topics(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [TopicOut.model_validate(t) for t in catalog_service.list_topics(db, user.id)]


@router.post("/topics", response_model=TopicOut, status_code=201)
def create_topic(data: TopicIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return TopicOut.model_validate(
        catalog_service.create_topic(db, user.id, data.name, data.description, data.keywords))


@router.patch("/topics/{row_id}", response_model=TopicOut)
def patch_topic(row_id: str, data: TopicIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    return TopicOut.model_validate(catalog_service.update_topic(
        db, user.id, row_id, name=data.name, description=data.description, keywords=data.keywords))


@router.delete("/topics/{row_id}", status_code=204)
def delete_topic(row_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    catalog_service.delete_topic(db, user.id, row_id)


@router.get("/occasions", response_model=list[OccasionOut])
def occasions(month: int = Query(..., ge=1, le=12), region: str | None = None,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = catalog_service.occasion_suggestions(db, month, region or user.region, user.id)
    return [OccasionOut.model_validate(r) for r in rows]
