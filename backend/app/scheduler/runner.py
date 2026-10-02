"""APScheduler worker process (Section 8.5).

Run standalone:   python -m app.scheduler.runner
Or in-process:    MM_SCHEDULER_ENABLED=true when starting the API (dev convenience).
"""
from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import init_db
from app.scheduler import jobs

logger = logging.getLogger("monthlymuse.scheduler.runner")

_scheduler: BackgroundScheduler | None = None


def build_scheduler() -> BackgroundScheduler:
    """Daily scan at 02:00 local + interval retries every 5 minutes (Section 8.5)."""
    scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)
    scheduler.add_job(jobs.run_all, CronTrigger(hour=2, minute=0,
                                                timezone=settings.scheduler_timezone),
                      id="daily_scan", misfire_grace_time=3600, coalesce=True)
    scheduler.add_job(jobs.run_all, IntervalTrigger(minutes=5),
                      id="interval_retry", misfire_grace_time=300, coalesce=True)
    return scheduler


def start_in_process() -> BackgroundScheduler | None:
    """Start the scheduler inside the API process (dev convenience; prod runs the worker)."""
    global _scheduler
    if not settings.scheduler_enabled:
        return None
    if _scheduler is not None:
        return _scheduler
    _scheduler = build_scheduler()
    _scheduler.start()
    logger.info("scheduler_started in_process tz=%s", settings.scheduler_timezone)
    return _scheduler


def stop_in_process() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def main() -> None:  # pragma: no cover - process entry point
    setup_logging()
    init_db()
    logger.info("worker_starting tz=%s", settings.scheduler_timezone)
    # catch up missed cycles immediately after downtime
    from app.scheduler.jobs import catch_up
    catch_up()
    scheduler = build_scheduler()
    scheduler.start()
    try:
        threading.Event().wait()   # run until interrupted
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
