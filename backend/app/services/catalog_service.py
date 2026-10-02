"""Audiences, topics, occasions, season mapping and seed loading (FR-02, FR-04)."""
from __future__ import annotations

import csv
import logging
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFound
from app.db.models import AudienceProfile, Occasion, Topic, User

logger = logging.getLogger("monthlymuse.catalog")

INDIA_SEASONS = {1: "winter", 2: "winter", 3: "summer", 4: "summer", 5: "summer",
                 6: "monsoon", 7: "monsoon", 8: "monsoon", 9: "monsoon",
                 10: "post-monsoon", 11: "post-monsoon", 12: "winter"}
NORTHERN_SEASONS = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                    6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}


def season_for(month: int, region: str = "India") -> str:
    if (region or "").lower() == "india":
        return INDIA_SEASONS.get(month, "mild")
    return NORTHERN_SEASONS.get(month, "mild")


def month_name(month: int) -> str:
    from datetime import datetime
    return datetime(2000, month, 1).strftime("%B")


# --- audiences ------------------------------------------------------------

def list_audiences(db: Session, user_id: str) -> list[AudienceProfile]:
    return list(db.scalars(select(AudienceProfile).where(AudienceProfile.user_id == user_id)
                           .order_by(AudienceProfile.name)).all())


def create_audience(db: Session, user_id: str, name: str, description: str | None) -> AudienceProfile:
    row = AudienceProfile(user_id=user_id, name=name, description=description)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_audience(db: Session, user_id: str, row_id: str, name: str | None,
                    description: str | None) -> AudienceProfile:
    row = _owned(db, AudienceProfile, row_id, user_id, "audience")
    if name:
        row.name = name
    if description is not None:
        row.description = description
    db.commit()
    db.refresh(row)
    return row


def delete_audience(db: Session, user_id: str, row_id: str) -> None:
    db.delete(_owned(db, AudienceProfile, row_id, user_id, "audience"))
    db.commit()


# --- topics ---------------------------------------------------------------

def list_topics(db: Session, user_id: str) -> list[Topic]:
    global_rows = list(db.scalars(select(Topic).where(Topic.user_id.is_(None)).order_by(Topic.name)).all())
    own = list(db.scalars(select(Topic).where(Topic.user_id == user_id).order_by(Topic.name)).all())
    return own + global_rows


def create_topic(db: Session, user_id: str, name: str, description: str | None,
                 keywords: list[str]) -> Topic:
    row = Topic(user_id=user_id, name=name, description=description, keywords=keywords)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_topic(db: Session, user_id: str, row_id: str, **fields) -> Topic:
    row = _owned(db, Topic, row_id, user_id, "topic")
    for key, value in fields.items():
        if value is not None:
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def delete_topic(db: Session, user_id: str, row_id: str) -> None:
    db.delete(_owned(db, Topic, row_id, user_id, "topic"))
    db.commit()


# --- occasions ------------------------------------------------------------

def list_occasions(db: Session, month: int | None = None, region: str = "India",
                   user_id: str | None = None) -> list[Occasion]:
    stmt = select(Occasion)
    if month:
        stmt = stmt.where(Occasion.month == month)
    rows = list(db.scalars(stmt.order_by(Occasion.month, Occasion.day)).all())
    wanted = (region or "global").lower()
    filtered = [r for r in rows if r.user_id == user_id or r.region in ("global", wanted)
                or r.region.lower() == wanted]
    return filtered


def occasion_suggestions(db: Session, month: int, region: str, user_id: str | None = None) -> list[Occasion]:
    """Occasions for the form's suggestion chips: month's rows, region first."""
    rows = list_occasions(db, month=month, region=region, user_id=user_id)
    region_rank = {"global": 1, (region or "").lower(): 0}
    return sorted(rows, key=lambda r: region_rank.get(r.region.lower(), 2))


# --- seeding --------------------------------------------------------------

def seed_global_data(db: Session, force: bool = False) -> dict:
    """Load occasions_seed.csv and topics_seed.csv when the tables are empty."""
    counts = {"occasions": 0, "topics": 0}
    if force or db.scalar(select(Occasion.id).limit(1)) is None:
        path = os.path.join(settings.data_dir, "occasions_seed.csv")
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    db.add(Occasion(
                        name=row["name"], month=int(row["month"]),
                        day=int(row["day"]) if row.get("day") else None,
                        region=row.get("region") or "global",
                        category=row.get("category") or "holiday",
                        description=row.get("description") or "",
                    ))
                    counts["occasions"] += 1
    if force or db.scalar(select(Topic.id).limit(1)) is None:
        path = os.path.join(settings.data_dir, "topics_seed.csv")
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    db.add(Topic(user_id=None, name=row["name"], description=row.get("description") or "",
                                 keywords=[k.strip() for k in (row.get("keywords") or "").split("|") if k.strip()]))
                    counts["topics"] += 1
    db.commit()
    logger.info("seed_global_data %s", counts)
    return counts


def _owned(db: Session, model, row_id: str, user_id: str, label: str):
    row = db.get(model, row_id)
    if row is None or getattr(row, "user_id", None) != user_id:
        raise NotFound(label)
    return row
