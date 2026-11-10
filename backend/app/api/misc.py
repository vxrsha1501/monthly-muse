"""Calendar .ics export, notifications and admin job monitoring."""
from __future__ import annotations

import hmac
import logging
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_admin_user, get_current_user, get_db
from app.core.errors import AppError, Conflict, NotFound, Unauthorized
from app.core.security import create_ics_token, decode_token
from app.db.models import JobRun, MonthlyPlan, Notification, PostCycle, User
from app.schemas.analytics import NotificationOut
from app.services import notification_service, plan_service

logger = logging.getLogger("monthlymuse.misc")

router = APIRouter(tags=["calendar", "notifications", "admin"])

# One tick at a time: concurrent cron calls get 409 instead of racing; inside
# the tick, job idempotency (existing request/cycle checks) prevents duplicates.
_scheduler_lock = threading.Lock()


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


# --------------------------------------------------------------- scheduler webhook
@router.post("/admin/scheduler/run")
def run_scheduler_tick(
    request: Request,
    db: Session = Depends(get_db),
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
) -> dict:
    """Authenticated external-cron entry point (review P12 / Priority 16).

    Serverless hosts suspend APScheduler, so an outside cron pokes this endpoint
    every few minutes.  It is authenticated with a constant-time-compared shared
    secret (``MM_SCHEDULER_WEBHOOK_TOKEN``, derived from the secret key when not
    set), runs the normal idempotent tick (due-cycle detection -> generation ->
    reminders -> audit rows), and refuses concurrent invocations with 409.
    Repeated calls are safe: a cycle that already has a queued/running/done
    scheduled request is never generated twice.
    """
    auth_header = request.headers.get("Authorization", "")
    bearer = auth_header[7:] if auth_header[:7].lower() == "bearer " else ""
    provided = x_webhook_token or bearer
    expected = settings.scheduler_token
    if not provided or not hmac.compare_digest(str(provided), expected):
        raise Unauthorized("Invalid scheduler token")

    if not _scheduler_lock.acquire(blocking=False):
        raise Conflict("A scheduler tick is already running")
    try:
        from app.scheduler.jobs import run_all
        result = run_all()
        rows = list(db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(5)).all())
        logger.info("scheduler_webhook_tick prepared=%s failed=%s reminders=%s",
                    result.get("prepared"), result.get("failed"), result.get("reminders"))
        return {
            "status": "ok",
            "result": result,
            "jobs": [{"job_name": r.job_name, "status": r.status.value,
                      "started_at": r.started_at.isoformat(),
                      "duration_ms": r.duration_ms, "error": r.error} for r in rows],
        }
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - boundary: report failures usefully
        logger.exception("scheduler_webhook_failed")
        raise AppError("scheduler_failed", f"Scheduler tick failed: {str(exc)[:200]}", 500)
    finally:
        _scheduler_lock.release()


@router.post("/admin/seed-demos")
def seed_demos(db: Session = Depends(get_db),
               x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
               authorization: str | None = Header(default=None)) -> dict:
    """(Re)create the four demo accounts and their October history.

    Hosted free tiers wipe the SQLite file on restart, so this is also called
    automatically at start-up. Authenticated with the scheduler token so it can
    be triggered from outside without exposing account data.
    """
    bearer = authorization[7:] if (authorization or "")[:7].lower() == "bearer " else ""
    provided = x_webhook_token or bearer
    if not provided or not hmac.compare_digest(str(provided), settings.scheduler_token):
        raise Unauthorized("Invalid seed token")
    from app.services.demo_seed import seed_demo_accounts
    return seed_demo_accounts(db)


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
        "llm_provider": settings.resolve_provider(),
        "llm_model": settings.model_for(settings.resolve_provider()),
        "environment": settings.environment,
    }
