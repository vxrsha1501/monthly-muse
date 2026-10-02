"""Analytics service (Section 3.6 + 16): usage, content, quality - all with sample sizes.

Rule for analytics: every statistic shows n; significance claims wait for counts.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.similarity import l2_normalize, similarity_matrix
from app.ai.stats import (chi_square_test, mean_pairwise_cosine, mean_reciprocal_rank,
                          point_biserial, shannon_entropy, wilson_interval)
from app.db.models import (CycleStatus, FeedbackEvent, GeneratedMessage, GenerationRequest,
                           MessageStatus, MonthlyPlan, PostCycle, User, UserPreferences)
from app.db.session import blob_to_vec
from app.schemas.analytics import (ActivityEvent, ContentOut, DashboardOut, MetricCard,
                                   NextAction, QualityOut, QuickStats, UpcomingCycle, UsageOut)

logger = logging.getLogger("monthlymuse.analytics")


def _displayed(db: Session, user_id: str) -> list[GeneratedMessage]:
    """Candidates that were actually shown to the user (ranked, not filtered)."""
    return list(db.scalars(
        select(GeneratedMessage).where(
            GeneratedMessage.user_id == user_id,
            GeneratedMessage.rank.is_not(None),
            GeneratedMessage.status.in_([MessageStatus.SHOWN, MessageStatus.SELECTED,
                                         MessageStatus.EDITED, MessageStatus.REJECTED]))
        .order_by(GeneratedMessage.created_at)).all())


def _selected(db: Session, user_id: str) -> list[GeneratedMessage]:
    return list(db.scalars(select(GeneratedMessage).where(
        GeneratedMessage.user_id == user_id,
        GeneratedMessage.status.in_([MessageStatus.SELECTED, MessageStatus.EDITED]))
        .order_by(GeneratedMessage.created_at)).all())


# --------------------------------------------------------------- dashboard

def dashboard(db: Session, user: User) -> DashboardOut:
    cycles = list(db.scalars(select(PostCycle).where(PostCycle.user_id == user.id)
                             .order_by(PostCycle.target_month)).all())
    plans = {p.id: p.name for p in db.scalars(
        select(MonthlyPlan).where(MonthlyPlan.user_id == user.id)).all()}

    now = datetime.now(timezone.utc)
    awaiting = next((c for c in cycles if c.status == CycleStatus.AWAITING_REVIEW), None)
    needs_attention = next((c for c in cycles if c.status == CycleStatus.NEEDS_ATTENTION), None)
    future = [c for c in cycles if c.post_at >= now and c.status in
              (CycleStatus.PLANNED, CycleStatus.GENERATING, CycleStatus.GENERATION_FAILED)]

    next_action = None
    focus = awaiting or needs_attention
    if focus is not None:
        next_action = NextAction(
            title=(f"{focus.target_month.strftime('%B')} post ready for review (3 candidates)"
                   if focus.status == CycleStatus.AWAITING_REVIEW
                   else f"{focus.target_month.strftime('%B')} post needs attention"),
            subtitle=f"Posting {focus.post_at.strftime('%d %b')} - tap to compare candidates",
            cta_label="Review candidates", cta_href="/review", status=focus.status.value,
        )
    elif future:
        nxt = future[0]
        next_action = NextAction(
            title="You're all set.",
            subtitle=f"Next post: {nxt.post_at.strftime('%d %B')}. We'll prepare it on "
                     f"{nxt.generate_at.strftime('%d %b')}.",
            cta_label="View calendar", cta_href="/calendar", status=nxt.status.value,
        )
    else:
        next_action = NextAction(title="Create your first Monthly Plan",
                                 subtitle="Set one up in 2 minutes and never start from scratch again.",
                                 cta_label="Create plan", cta_href="/plans")

    this_month = next((c for c in cycles if c.target_month.year == now.year
                       and c.target_month.month == now.month), None)
    upcoming = [UpcomingCycle(id=c.id, target_month=c.target_month.strftime("%Y-%m"),
                              post_at=c.post_at, generate_at=c.generate_at,
                              status=c.status.value, plan_name=plans.get(c.plan_id))
                for c in (future + [c for c in cycles if c.status in
                                    (CycleStatus.AWAITING_REVIEW, CycleStatus.SCHEDULED)])[:4]]

    stats = quick_stats(db, user.id)
    activity = recent_activity(db, user.id)
    return DashboardOut(
        next_action=next_action,
        this_month=UpcomingCycle(id=this_month.id, target_month=this_month.target_month.strftime("%Y-%m"),
                                 post_at=this_month.post_at, generate_at=this_month.generate_at,
                                 status=this_month.status.value, plan_name=plans.get(this_month.plan_id))
        if this_month else None,
        upcoming=upcoming, quick_stats=stats, recent_activity=activity,
    )


def quick_stats(db: Session, user_id: str) -> QuickStats:
    messages = list(db.scalars(select(GeneratedMessage).where(GeneratedMessage.user_id == user_id)).all())
    displayed = [m for m in messages if m.rank is not None]
    selected = [m for m in messages if m.status == MessageStatus.SELECTED]
    n_batches = len({m.request_id for m in displayed})
    selection_rate = (len(selected) / len(displayed)) if displayed else None
    ci = wilson_interval(len(selected), len(displayed)) if displayed else None
    novelties = [m.f_novelty for m in selected if m.f_novelty is not None]
    tone_mix = Counter(m.intended_tone for m in selected if m.intended_tone)
    return QuickStats(
        messages_generated=len(messages),
        selection_rate=round(selection_rate, 3) if selection_rate is not None else None,
        selection_rate_ci=[round(ci[0], 3), round(ci[1], 3)] if ci else None,
        avg_novelty=round(float(np.mean(novelties)), 3) if novelties else None,
        tone_mix=dict(tone_mix), n=len(displayed) or n_batches,
    )


def recent_activity(db: Session, user_id: str, limit: int = 5) -> list[ActivityEvent]:
    events = list(db.scalars(select(FeedbackEvent).where(FeedbackEvent.user_id == user_id)
                             .order_by(FeedbackEvent.created_at.desc()).limit(limit)).all())
    labels = {"select": "Selected a candidate", "edit": "Edited a candidate",
              "reject": "Rejected a candidate", "like": "Liked a candidate",
              "dislike": "Disliked a candidate", "copy": "Copied a candidate",
              "regenerate": "Regenerated the batch", "ignore": "Left a candidate unselected"}
    return [ActivityEvent(id=e.id, kind=e.event_type.value,
                          label=labels.get(e.event_type.value, e.event_type.value),
                          created_at=e.created_at) for e in events]


# --------------------------------------------------------------- usage

def usage(db: Session, user_id: str) -> UsageOut:
    messages = list(db.scalars(select(GeneratedMessage).where(GeneratedMessage.user_id == user_id)).all())
    requests = list(db.scalars(select(GenerationRequest).where(GenerationRequest.user_id == user_id)
                               .order_by(GenerationRequest.created_at)).all())
    displayed = [m for m in messages if m.rank is not None]
    selected = [m for m in messages if m.status == MessageStatus.SELECTED]
    rejects = [m for m in messages if m.status == MessageStatus.REJECTED]
    regenerations = len([r for r in requests if r.parent_request_id])
    batches = max(len({m.request_id for m in displayed}), 1)
    edits = [m for m in messages if m.status in (MessageStatus.EDITED,) or
             (m.final_text and m.final_text != m.text)]

    selection_rate = len(selected) / len(displayed) if displayed else None
    ci = wilson_interval(len(selected), len(displayed)) if displayed else (0.0, 1.0)

    weekly: dict[str, int] = defaultdict(int)
    now = datetime.now(timezone.utc)
    for r in requests:
        if r.created_at and r.created_at >= now - timedelta(weeks=8):
            key = r.created_at.strftime("%Y-W%W")
            weekly[key] += 1

    # time to approve: request done -> select event
    durations = []
    for e in db.scalars(select(FeedbackEvent).where(FeedbackEvent.user_id == user_id,
                                                    FeedbackEvent.event_type == "select")).all():
        if e.request_id:
            req = db.get(GenerationRequest, e.request_id)
            if req and req.created_at and e.created_at:
                durations.append((e.created_at - req.created_at).total_seconds() / 3600)

    return UsageOut(
        cards=[
            MetricCard(label="Messages generated", value=len(messages), n=len(messages)),
            MetricCard(label="Batches", value=len(requests), n=len(requests)),
            MetricCard(label="Selection rate",
                       value=round(selection_rate, 3) if selection_rate is not None else None,
                       detail=f"95% CI {ci[0]:.0%}-{ci[1]:.0%}" if displayed else "needs displayed candidates",
                       n=len(displayed)),
            MetricCard(label="Rejections", value=len(rejects), n=len(displayed)),
        ],
        weekly=[{"week": k, "generated": v} for k, v in sorted(weekly.items())],
        regeneration_rate=round(regenerations / batches, 3) if batches else None,
        edit_rate=round(len(edits) / len(displayed), 3) if displayed else None,
        avg_time_to_approve_hours=round(float(np.mean(durations)), 1) if durations else None,
    )


# --------------------------------------------------------------- content

def content(db: Session, user_id: str) -> ContentOut:
    selected = _selected(db, user_id)
    displayed = _displayed(db, user_id)
    messages = [m for m in displayed if m.rank is not None]

    tone_counts = Counter(m.intended_tone for m in selected if m.intended_tone)
    topic_counts: Counter = Counter()
    for m in messages:
        req = db.get(GenerationRequest, m.request_id)
        if req:
            topic_counts[req.input.get("topic") or req.input.get("_brief_topic") or "untitled"] += 1

    novelties = [(m.created_at.strftime("%Y-%m"), m.f_novelty) for m in selected if m.f_novelty is not None]
    by_month: dict[str, list[float]] = defaultdict(list)
    for month, value in novelties:
        by_month[month].append(value)
    novelty_trend = [{"month": k, "avg_novelty": round(float(np.mean(v)), 3), "n": len(v)}
                     for k, v in sorted(by_month.items())]

    # repetition alerts: pairs of recent selected posts with cos >= 0.85
    recent = selected[-6:]
    embeddings = [blob_to_vec(m.embedding) for m in recent]
    pairs = []
    valid = [(m, e) for m, e in zip(recent, embeddings) if e is not None]
    if len(valid) >= 2:
        M = l2_normalize(np.vstack([e for _, e in valid]))
        S = similarity_matrix(M, M)
        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                if S[i, j] >= 0.85:
                    pairs.append({"a": valid[i][0].id, "b": valid[j][0].id,
                                  "cosine": round(float(S[i, j]), 3)})

    lengths = [{"month": m.created_at.strftime("%Y-%m"), "words": m.word_count}
               for m in selected if m.word_count]

    heatmap: dict[tuple[str, str], int] = defaultdict(int)
    for m in selected:
        if m.intended_tone:
            heatmap[(m.created_at.strftime("%Y-%m"), m.intended_tone)] += 1

    return ContentOut(
        tone_distribution=dict(tone_counts),
        topic_distribution=dict(topic_counts.most_common(8)),
        entropy=round(shannon_entropy(list(tone_counts.values())), 3) if tone_counts else None,
        entropy_n=len(selected),
        avg_novelty=round(float(np.mean([v for _, v in novelties])), 3) if novelties else None,
        novelty_trend=novelty_trend,
        repetition_alerts=pairs,
        length_distribution=lengths,
        month_tone_heatmap=[{"month": k[0], "tone": k[1], "count": v} for k, v in sorted(heatmap.items())],
    )


# --------------------------------------------------------------- quality

def quality(db: Session, user_id: str) -> QualityOut:
    requests = list(db.scalars(select(GenerationRequest).where(GenerationRequest.user_id == user_id)).all())
    displayed = _displayed(db, user_id)
    selected = [m for m in displayed if m.status == MessageStatus.SELECTED]

    scores = np.array([m.score for m in displayed if m.score is not None], dtype=float)
    flags = np.array([1.0 if m.status == MessageStatus.SELECTED else 0.0
                      for m in displayed if m.score is not None], dtype=float)

    # top-1 agreement and MRR per batch
    per_batch: dict[str, list[GeneratedMessage]] = defaultdict(list)
    for m in displayed:
        per_batch[m.request_id].append(m)
    agreements, rrs = [], []
    for batch in per_batch.values():
        chosen = next((m for m in batch if m.status == MessageStatus.SELECTED), None)
        if chosen is not None and chosen.rank:
            agreements.append(1.0 if chosen.rank == 1 else 0.0)
            rrs.append(1.0 / chosen.rank)

    latencies = sorted(r.latency_ms for r in requests if r.status.value == "DONE" and r.latency_ms)
    failures = len([r for r in requests if r.status.value == "FAILED"])
    fallbacks = len([r for r in requests if r.provider == "template"])
    done = len([r for r in requests if r.status.value in ("DONE", "FAILED")]) or 1

    # tone x selection contingency with Wilson CIs
    by_tone: dict[str, dict[str, int]] = defaultdict(lambda: {"selected": 0, "not_selected": 0})
    for m in displayed:
        tone = m.intended_tone or "unknown"
        if m.status == MessageStatus.SELECTED:
            by_tone[tone]["selected"] += 1
        else:
            by_tone[tone]["not_selected"] += 1
    rows = []
    table = []
    for tone, counts in sorted(by_tone.items()):
        n = counts["selected"] + counts["not_selected"]
        ci = wilson_interval(counts["selected"], n)
        rows.append({"tone": tone, "n": n, "selected": counts["selected"],
                     "rate": round(counts["selected"] / n, 3) if n else 0.0,
                     "ci": [round(ci[0], 3), round(ci[1], 3)]})
        table.append([counts["selected"], counts["not_selected"]])
    test = chi_square_test(np.array(table)) if len(table) >= 2 else {"insufficient": True}

    return QualityOut(
        avg_selected_score=round(float(np.mean([m.score for m in selected])), 3) if selected else None,
        score_selection_correlation=round(point_biserial(scores, flags), 3) if len(scores) >= 3 else None,
        top1_agreement=round(float(np.mean(agreements)), 3) if agreements else None,
        mrr=round(mean_reciprocal_rank(rrs), 3) if rrs else None,
        latency_p50=int(np.percentile(latencies, 50)) if latencies else None,
        latency_p95=int(np.percentile(latencies, 95)) if latencies else None,
        llm_failure_rate=round(failures / done, 3),
        fallback_rate=round(fallbacks / done, 3),
        tone_selection_test=test,
        selection_by_tone=rows,
        n=len(displayed),
    )
