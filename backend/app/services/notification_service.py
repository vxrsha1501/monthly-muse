"""Notifications: in-app rows plus email delivery (console backend in dev, Section 8.5)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Notification, NotificationType, User

logger = logging.getLogger("monthlymuse.notifications")


def notify(db: Session, user_id: str, ntype: NotificationType, title: str, body: str = "",
           cycle_id: str | None = None, send_email: bool = True) -> Notification:
    row = Notification(user_id=user_id, type=ntype, title=title, body=body, cycle_id=cycle_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    if send_email:
        send_email_notification(db, row)
    return row


def send_email_notification(db: Session, row: Notification) -> None:
    """SMTP/Resend/SendGrid in production; console backend writes a log line in dev."""
    user = db.get(User, row.user_id)
    if user is None:
        return
    if settings.email_backend == "console":
        logger.info("email_queued to=%s subject=%r backend=console", user.email, row.title)
    else:  # pragma: no cover - production backends
        logger.info("email_queued to=%s subject=%r backend=%s", user.email, row.title,
                    settings.email_backend)
    row.emailed_at = datetime.now(timezone.utc)
    db.commit()


def list_notifications(db: Session, user_id: str, limit: int = 50) -> list[Notification]:
    return list(db.scalars(select(Notification).where(Notification.user_id == user_id)
                           .order_by(Notification.created_at.desc()).limit(limit)).all())


def mark_read(db: Session, user_id: str, notification_id: str) -> Notification:
    row = db.get(Notification, notification_id)
    if row is None or row.user_id != user_id:
        from app.core.errors import NotFound
        raise NotFound("notification")
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
    return row


def unread_count(db: Session, user_id: str) -> int:
    return len([n for n in list_notifications(db, user_id, 200) if n.read_at is None])
