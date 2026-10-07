"""Message history: text search plus semantic search (FR-16)."""
from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.similarity import l2_normalize, similarity_matrix
from app.core.errors import NotFound
from app.core.runtime import Runtime
from app.db.models import FeedbackEvent, GeneratedMessage, GenerationRequest, MessageStatus, User
from app.db.session import blob_to_vec
from app.schemas.generation import HistoryItemOut

logger = logging.getLogger("monthlymuse.history")

VISIBLE_STATUSES = (MessageStatus.SHOWN, MessageStatus.SELECTED, MessageStatus.EDITED,
                    MessageStatus.REJECTED, MessageStatus.ARCHIVED)


def request_months(db: Session, user_id: str) -> dict[str, str]:
    """request_id -> posting month the batch was written for (e.g. '2026-11')."""
    months: dict[str, str] = {}
    for r in db.scalars(select(GenerationRequest).where(GenerationRequest.user_id == user_id)):
        m = (r.input or {}).get("month")
        if m:
            months[r.id] = str(m)
    return months


def content_month(row: GeneratedMessage, months: dict[str, str] | None = None,
                  db: Session | None = None) -> str | None:
    """The month a message is *about* (its generation payload), never its insert time.

    Posts are generated ahead of time (November posts are written in October), so
    created_at must not be used as a content-month label anywhere in the UI/analytics.
    """
    m = None
    if months is not None:
        m = months.get(row.request_id)
    elif db is not None:
        req = db.get(GenerationRequest, row.request_id)
        m = (req.input or {}).get("month") if req else None
    if not m and row.created_at:
        return row.created_at.strftime("%Y-%m")
    return m


def search_history(db: Session, user_id: str, *, q: str | None = None, tone: str | None = None,
                   topic: str | None = None, month: str | None = None, status: str | None = None,
                   min_score: float | None = None, max_score: float | None = None,
                   semantic: bool = False, page: int = 1, page_size: int = 20
                   ) -> tuple[list[HistoryItemOut], int]:
    stmt = select(GeneratedMessage).where(GeneratedMessage.user_id == user_id,
                                          GeneratedMessage.status.in_(VISIBLE_STATUSES))
    if tone:
        stmt = stmt.where(GeneratedMessage.intended_tone == tone)
    if status:
        stmt = stmt.where(GeneratedMessage.status == status)
    if min_score is not None:
        stmt = stmt.where(GeneratedMessage.score >= min_score)
    if max_score is not None:
        stmt = stmt.where(GeneratedMessage.score <= max_score)
    if q and not semantic:
        like = f"%{q}%"
        stmt = stmt.where(GeneratedMessage.text.ilike(like) | GeneratedMessage.final_text.ilike(like))

    rows = list(db.scalars(stmt).all())
    months_by_request = request_months(db, user_id)
    if month:
        rows = [r for r in rows if content_month(r, months_by_request) == month]

    if topic:
        wanted = topic.lower()
        filtered = []
        for row in rows:
            req = db.get(GenerationRequest, row.request_id)
            blob = " ".join(str(req.input.get(k, "")) for k in ("topic", "_brief_topic", "occasion")) if req else ""
            if wanted in blob.lower():
                filtered.append(row)
        rows = filtered

    scores: dict[str, float] = {}
    if semantic and q:
        embedder = Runtime.init().embedder
        q_vec = embedder.embed([q])
        with_emb = [r for r in rows if blob_to_vec(r.embedding) is not None]
        if with_emb:
            H = l2_normalize(np.vstack([blob_to_vec(r.embedding) for r in with_emb]))
            sims = similarity_matrix(q_vec, H)[0]
            for row, s in zip(with_emb, sims):
                scores[row.id] = float(s)
            rows = [r for r in rows if r.id in scores]
        else:
            rows = []
        rows.sort(key=lambda r: -scores[r.id])
    elif q:
        rows.sort(key=lambda r: r.created_at, reverse=True)
    else:
        rows.sort(key=lambda r: r.created_at, reverse=True)

    total = len(rows)
    start_idx = max(0, (max(page, 1) - 1) * page_size)
    page_rows = rows[start_idx:start_idx + page_size]

    items = [HistoryItemOut(
        id=row.id, text=row.final_text or row.text,
        month=content_month(row, months_by_request),
        topic=_topic_of(db, row), tone=row.intended_tone, style_label=row.style_label,
        platform=_platform_of(db, row), status=row.status.value, score=row.score,
        novelty=row.f_novelty, created_at=row.created_at,
        similarity=scores.get(row.id),
    ) for row in page_rows]
    return items, total


def message_detail(db: Session, user_id: str, message_id: str) -> dict:
    row = db.get(GeneratedMessage, message_id)
    if row is None or row.user_id != user_id:
        raise NotFound("message")
    req = db.get(GenerationRequest, row.request_id)
    batch = [m for m in _batch(db, row.request_id)] if req else []
    events = list(db.scalars(select(FeedbackEvent).where(FeedbackEvent.message_id == row.id)
                             .order_by(FeedbackEvent.created_at)).all())
    return {
        "message": row,
        "request": req,
        "batch": batch,
        "feedback": [{
            "id": e.id, "event_type": e.event_type.value, "reward": e.reward,
            "reason": e.reason, "edit_distance": e.edit_distance,
            "created_at": e.created_at.isoformat(),
        } for e in events],
        "inputs": (req.input if req else {}),
        "chosen": next((m.id for m in batch if m.status == MessageStatus.SELECTED), None),
    }


def reuse_as_start(db: Session, user_id: str, message_id: str) -> dict:
    """'Generate similar': recover the original inputs of a past batch."""
    row = db.get(GeneratedMessage, message_id)
    if row is None or row.user_id != user_id:
        raise NotFound("message")
    req = db.get(GenerationRequest, row.request_id)
    return dict(req.input) if req else {}


def _batch(db: Session, request_id: str) -> list[GeneratedMessage]:
    return list(db.scalars(select(GeneratedMessage).where(GeneratedMessage.request_id == request_id)
                           .order_by(GeneratedMessage.rank.is_(None), GeneratedMessage.rank)).all())


def _topic_of(db: Session, row: GeneratedMessage) -> str | None:
    req = db.get(GenerationRequest, row.request_id)
    if not req:
        return None
    return req.input.get("topic") or req.input.get("_brief_topic")


def _platform_of(db: Session, row: GeneratedMessage) -> str | None:
    req = db.get(GenerationRequest, row.request_id)
    if not req:
        return None
    return req.input.get("platform") or req.input.get("_brief_platform") or "instagram"
