"""Regression tests: the plan timing model (audit Task 7 / review P11, T8).

    POST_DATE (cycle.post_at) - lead_days (default 7) = PREPARATION_DATE (cycle.generate_at)

Covers: normal month, month/year boundary, February + leap year, timezone,
paused/resumed plans, missed scheduler run (catch-up), manual generation,
rescheduled posts, legacy lead=0 repair, dashboard/API agreement, and
T-3 / T-1 reminders measured against the POST date only.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.db.session import SessionLocal
from app.services.plan_service import (CYCLE_HORIZON, DEFAULT_LEAD_DAYS, compute_cycle_dates,
                                       effective_lead, ensure_cycles, preparation_at)


def _user(tz="Asia/Kolkata"):
    return SimpleNamespace(timezone=tz)


# ------------------------------------------------------------ pure date math
def test_post_15_october_prepares_8_october():
    triples = compute_cycle_dates(_user(), 15, time(10, 0), 7, start_from=date(2026, 10, 1))
    targets = {post.date(): prep.date() for _, post, prep in triples}
    post, prep = date(2026, 10, 15), date(2026, 10, 8)
    assert targets[post] == prep
    assert (post - prep).days == 7


def test_explicit_start_date_is_independent_of_the_real_clock():
    """A fixed anchor must keep its own month however much time has passed.

    The suite used to fail from November onwards because the anchor month was
    compared against the wall clock and silently dropped.
    """
    triples = compute_cycle_dates(_user(), 15, time(10, 0), 7, start_from=date(2026, 10, 1))
    assert triples[0][0] == date(2026, 10, 1)      # the anchor month is always produced
    assert len(triples) == CYCLE_HORIZON


def test_every_cycle_is_exactly_lead_days_before_posting():
    for start in (date(2026, 1, 1), date(2026, 11, 15), date(2027, 12, 31)):
        for lead in (1, 7, 14, 28):
            for _, post, prep in compute_cycle_dates(_user(), 12, time(9, 0), lead,
                                                     start_from=start):
                assert (post - prep).days == lead, (start, lead, post, prep)


def test_month_boundary_clamps_to_last_day():
    triples = compute_cycle_dates(_user(), 31, time(10, 0), 7, start_from=date(2026, 1, 10))
    by_month = {post.month: (post, prep) for _, post, prep in triples}
    assert by_month[2][0].day == 28          # 2026 not a leap year
    assert by_month[4][0].day == 30          # April
    assert (by_month[4][0] - by_month[4][1]).days == 7


def test_leap_year_february_29():
    triples = compute_cycle_dates(_user(), 29, time(10, 0), 7, start_from=date(2028, 1, 5))
    post, prep = next((p, g) for _, p, g in triples if p.month == 2)
    assert post.day == 29                      # 2028 IS a leap year
    assert (post - prep).days == 7


def test_year_boundary_preparation_falls_in_previous_year():
    triples = compute_cycle_dates(_user(), 3, time(10, 0), 7, start_from=date(2026, 12, 20))
    post, prep = next((p, g) for _, p, g in triples if p.year == 2027)
    assert prep.year == 2026
    assert prep < post and (post - prep).days == 7


def test_timezone_is_honoured_posting_moment_is_local_midmorning():
    # 10:00 Asia/Kolkata == 04:30 UTC
    triples = compute_cycle_dates(_user("Asia/Kolkata"), 15, time(10, 0), 7,
                                  start_from=date(2026, 10, 1))
    post = triples[0][1]
    assert post.utcoffset() == timedelta(0)        # stored in UTC
    assert (post.hour, post.minute) == (4, 30)     # 10:00 IST


def test_other_timezone_changes_the_utc_instant():
    ist = compute_cycle_dates(_user("Asia/Kolkata"), 15, time(10, 0), 7, start_from=date(2026, 10, 1))[0][1]
    nyc = compute_cycle_dates(_user("America/New_York"), 15, time(10, 0), 7, start_from=date(2026, 10, 1))[0][1]
    assert ist != nyc
    # both were built from the same (post, prep) helper - recompute prep for tz check
    ist_prep = compute_cycle_dates(_user("Asia/Kolkata"), 15, time(10, 0), 7,
                                   start_from=date(2026, 10, 1))[0][2]
    nyc_prep = compute_cycle_dates(_user("America/New_York"), 15, time(10, 0), 7,
                                   start_from=date(2026, 10, 1))[0][2]
    assert (ist - ist_prep).days == 7
    assert (nyc - nyc_prep).days == 7


def test_legacy_zero_lead_never_means_same_day():
    assert effective_lead(0) == DEFAULT_LEAD_DAYS == 7
    assert effective_lead(None) == 7
    assert effective_lead("junk") == 7
    assert effective_lead(5) == 5
    post = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    assert preparation_at(post, 0).date() == date(2026, 10, 8)
    assert preparation_at(post, 0) != post


def _plan_cycles(client, auth_headers, plan_id: str) -> list[dict]:
    return [c for c in client.get("/api/v1/cycles", headers=auth_headers).json()
            if c["plan_id"] == plan_id]


# ------------------------------------------------------------ API + DB
def _make_plan(client, auth_headers, **over):
    body = {"name": "Timing plan", "tones": ["warm"], "purpose": "inform",
            "platform": "instagram", "post_day": 15, "lead_days": 7,
            "cta_text": "Read more"}
    body.update(over)
    return client.post("/api/v1/monthly-plans", headers=auth_headers, json=body)


def test_api_plan_cycles_are_prep_minus_7(client, auth_headers):
    resp = _make_plan(client, auth_headers)
    assert resp.status_code == 201, resp.text
    cycles = client.get("/api/v1/cycles", headers=auth_headers).json()
    assert cycles
    for c in cycles:
        post = datetime.fromisoformat(c["post_at"].replace("Z", "+00:00"))
        prep = datetime.fromisoformat(c["generate_at"].replace("Z", "+00:00"))
        assert (post - prep).days == 7
        assert prep < post


def test_api_rejects_zero_lead_days(client, auth_headers):
    resp = _make_plan(client, auth_headers, lead_days=0)
    assert resp.status_code == 422, resp.text


def test_plan_out_next_generate_matches_cycle(client, auth_headers):
    plan = _make_plan(client, auth_headers).json()
    cycles = [c for c in client.get("/api/v1/cycles", headers=auth_headers).json()
              if c["status"] == "PLANNED"]
    assert cycles
    nxt = client.get(f"/api/v1/monthly-plans/{plan['id']}", headers=auth_headers).json()
    assert nxt["next_generate_at"] is not None
    assert nxt["next_generate_at"] == cycles[0]["generate_at"]


def test_dashboard_prep_date_matches_database_and_differs_from_post(client, auth_headers):
    _make_plan(client, auth_headers)
    summary = client.get("/api/v1/analytics/summary", headers=auth_headers).json()
    upcoming = summary["upcoming"]
    assert upcoming, "the new plan must appear on the dashboard"
    row = upcoming[0]
    post = datetime.fromisoformat(row["post_at"].replace("Z", "+00:00"))
    prep = datetime.fromisoformat(row["generate_at"].replace("Z", "+00:00"))
    assert (post - prep).days == 7
    subtitle = summary["next_action"]["subtitle"]
    # both branches (future prep / overdue prep) quote the real preparation date
    assert prep.strftime("%d %b") in subtitle, subtitle
    if "prepare it on" in subtitle:
        assert prep.date() != post.date(), "dashboard must not show post day as prep day"


def test_rescheduled_post_moves_preparation_with_it(client, auth_headers):
    plan = _make_plan(client, auth_headers).json()
    cycles = client.get("/api/v1/cycles", headers=auth_headers).json()
    target = next(c for c in cycles if c["status"] == "PLANNED")
    new_post = datetime(2030, 6, 20, 10, 0, tzinfo=timezone.utc)
    resp = client.patch(f"/api/v1/cycles/{target['id']}", headers=auth_headers,
                        json={"post_at": new_post.isoformat()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    prep = datetime.fromisoformat(body["generate_at"].replace("Z", "+00:00"))
    assert prep == new_post - timedelta(days=7)


def test_paused_plan_still_keeps_date_semantics(client, auth_headers):
    plan = _make_plan(client, auth_headers).json()
    paused = client.post(f"/api/v1/monthly-plans/{plan['id']}/pause", headers=auth_headers)
    assert paused.status_code == 200 and paused.json()["is_active"] is False
    cycles = client.get("/api/v1/cycles", headers=auth_headers).json()
    for c in cycles:
        post = datetime.fromisoformat(c["post_at"].replace("Z", "+00:00"))
        prep = datetime.fromisoformat(c["generate_at"].replace("Z", "+00:00"))
        assert (post - prep).days == 7
    resumed = client.post(f"/api/v1/monthly-plans/{plan['id']}/resume", headers=auth_headers)
    assert resumed.status_code == 200 and resumed.json()["is_active"] is True


def test_legacy_cycle_with_same_day_prep_is_repaired(client, auth_headers):
    plan = _make_plan(client, auth_headers).json()
    db = SessionLocal()
    try:
        from app.db.models import PostCycle
        cycle = db.scalars(select(PostCycle).where(PostCycle.plan_id == plan["id"])
                           .order_by(PostCycle.target_month)).first()
        cycle.generate_at = cycle.post_at          # simulate the reported bug
        db.commit()
        cycle_id, plan_id = cycle.id, cycle.plan_id
    finally:
        db.close()

    db = SessionLocal()
    try:
        from app.db.models import MonthlyPlan, User
        plan = db.get(MonthlyPlan, plan_id)
        user = db.get(User, plan.user_id)
        ensure_cycles(db, user, plan)
        cycle = db.get(PostCycle, cycle_id)
        assert cycle.generate_at == cycle.post_at - timedelta(days=7)
    finally:
        db.close()


# ------------------------------------------------------------ scheduler
def test_due_window_uses_preparation_date_not_post_date(client, auth_headers):
    from app.scheduler.jobs import prepare_due_cycles
    plan = _make_plan(client, auth_headers).json()
    db = SessionLocal()
    try:
        from app.db.models import PostCycle
        cycle = db.scalars(select(PostCycle).where(PostCycle.plan_id == plan["id"])
                           .order_by(PostCycle.target_month)).first()
        future_prep = datetime.now(timezone.utc) + timedelta(days=3)
        cycle.generate_at = future_prep
        cycle.post_at = future_prep + timedelta(days=7)
        db.commit()
        cycle_id = cycle.id
    finally:
        db.close()

    # preparation not due yet -> this cycle must NOT be generated even though
    # its posting date is only 10 days away (generation keys off generate_at)
    prepare_due_cycles()

    db = SessionLocal()
    try:
        from app.db.models import GenerationRequest, PostCycle
        n = db.scalar(select(GenerationRequest.id).where(
            GenerationRequest.cycle_id == cycle_id).limit(1))
        assert n is None, "generation must trigger on generate_at (T-7), not post_at"
    finally:
        db.close()


def test_missed_preparation_runs_on_catch_up(client, auth_headers):
    from app.scheduler.jobs import catch_up
    plan = _make_plan(client, auth_headers).json()
    db = SessionLocal()
    try:
        from app.db.models import PostCycle
        cycle = db.scalars(select(PostCycle).where(PostCycle.plan_id == plan["id"])
                           .order_by(PostCycle.target_month)).first()
        cycle.generate_at = datetime.now(timezone.utc) - timedelta(days=3)
        db.commit()
        cycle_id = cycle.id
    finally:
        db.close()
    result = catch_up()
    assert result["prepared"] >= 1
    db = SessionLocal()
    try:
        from app.db.models import CycleStatus, PostCycle
        cycle = db.get(PostCycle, cycle_id)
        assert cycle.status == CycleStatus.AWAITING_REVIEW
    finally:
        db.close()


def test_t3_and_t1_reminders_are_relative_to_post_date(client, auth_headers):
    from app.scheduler.jobs import prepare_due_cycles, send_reminders
    plan = _make_plan(client, auth_headers).json()
    db = SessionLocal()
    try:
        from app.db.models import PostCycle
        cycle = db.scalars(select(PostCycle).where(PostCycle.plan_id == plan["id"])
                           .order_by(PostCycle.target_month)).first()
        cycle.generate_at = datetime.now(timezone.utc) - timedelta(days=5)   # prep long done
        cycle.post_at = datetime.now(timezone.utc) + timedelta(days=2)       # T-2 from POST
        db.commit()
        cycle_id = cycle.id
    finally:
        db.close()

    prepare_due_cycles()
    send_reminders()

    db = SessionLocal()
    try:
        from app.db.models import Notification, NotificationType
        titles = [n.title for n in db.scalars(select(Notification).where(
            Notification.cycle_id == cycle_id, Notification.type == NotificationType.REMINDER))]
        assert any("awaiting review" in t for t in titles), titles   # T-3 window
        assert not any("due tomorrow" in t for t in titles), titles  # not yet T-1
        # move to T-1 relative to the POST date
        from app.db.models import PostCycle
        c = db.get(PostCycle, cycle_id)
        c.post_at = datetime.now(timezone.utc) + timedelta(hours=20)
        db.commit()
    finally:
        db.close()

    send_reminders()
    db = SessionLocal()
    try:
        from app.db.models import Notification, NotificationType, PostCycle
        rows = list(db.scalars(select(Notification).where(
            Notification.cycle_id == cycle_id, Notification.type == NotificationType.REMINDER)))
        titles = [n.title for n in rows]
        assert any("awaiting review" in t for t in titles)
        assert any("due tomorrow" in t for t in titles), titles      # T-1 fires
        # idempotent: another tick must not duplicate either reminder
    finally:
        db.close()
    send_reminders()
    db = SessionLocal()
    try:
        from app.db.models import Notification, NotificationType
        rows = list(db.scalars(select(Notification).where(
            Notification.cycle_id == cycle_id, Notification.type == NotificationType.REMINDER)))
        assert len(rows) == 2, [r.title for r in rows]
    finally:
        db.close()
