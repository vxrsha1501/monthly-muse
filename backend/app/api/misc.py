"""Calendar .ics export, notifications and admin job monitoring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_admin_user, get_current_user, get_db
from app.core.errors import NotFound
from app.core.security import create_ics_token, decode_token
from app.db.models import JobRun, MonthlyPlan, Notification, PostCycle, User
from app.schemas.analytics import NotificationOut
from app.services import notification_service, plan_service

router = APIRouter(tags=["calendar", "notifications", "admin"])


# --------------------------------------------------------------- .ics feed

def _ics_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(db: Session, user_id: str) -> str:
    cycles = plan_service.list_cycles(db, user_id)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//MonthlyMuse//EN", "CALSCALE:GREGORIAN"]
    for cycle in cycles:
        if cycle.status in ("SKIPPED",):
            continue
        start = cycle.post_at.astimezone(timezone.utc)
        end = start + timedelta(hours=1)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        plan = db.get(MonthlyPlan, cycle.plan_id)
        summary = f"MonthlyMuse post: {plan.name if plan else 'monthly post'}"
        description = f"Status: {cycle.status.value}. Prepare and post this month's message."
        lines += [
            "BEGIN:VEVENT",
            f"UID:{cycle.id}@monthlymuse",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(description)}",
            "BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY",
            f"DESCRIPTION:{_ics_escape('MonthlyMuse post due tomorrow')}", "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


@router.get("/calendar.ics")
def calendar_ics(token: str = Query(...), db: Session = Depends(get_db)) -> Response:
    payload = decode_token(token, expected_type="ics")
    user_id = payload.get("sub", "")
    body = build_ics(db, user_id)
    return Response(content=body, media_type="text/calendar",
                    headers={"Content-Disposition": 'attachment; filename="monthlymuse.ics"'})


@router.get("/calendar-token")
def calendar_token(user: User = Depends(get_current_user)) -> dict:
    token = create_ics_token(user.id)
    return {"token": token, "url": f"/api/v1/calendar.ics?token={token}"}


# --------------------------------------------------------------- notifications

@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    return [NotificationOut.model_validate(n)
            for n in notification_service.list_notifications(db, user.id)]


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: str,
              user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    return NotificationOut.model_validate(notification_service.mark_read(db, user.id, notification_id))


@router.get("/notifications/unread-count")
def unread_count(user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> dict:
    return {"unread": notification_service.unread_count(db, user.id)}


# --------------------------------------------------------------- admin

@router.get("/admin/jobs")
def admin_jobs(limit: int = 50, admin: User = Depends(get_admin_user),
               db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)).all())
    return {"jobs": [{"id": r.id, "job_name": r.job_name, "cycle_id": r.cycle_id,
                      "status": r.status.value, "attempt": r.attempt,
                      "started_at": r.started_at.isoformat(),
                      "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                      "duration_ms": r.duration_ms, "error": r.error} for r in rows]}


@router.get("/admin/health")
def health(db: Session = Depends(get_db)) -> dict:
    from app.core.runtime import Runtime
    from app.core.config import settings
    try:
        n = db.scalar(select(PostCycle.id).limit(1))
        db_ok = True
    except Exception:  # pragma: no cover
        db_ok = False
    runtime = Runtime.init()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "embedder": runtime.embedder.name if runtime.embedder else None,
        "tone_labels": len(runtime.tone_classifier.labels) if runtime.tone_classifier else 0,
        "llm_provider": settings.llm_provider,
        "environment": settings.environment,
    }
