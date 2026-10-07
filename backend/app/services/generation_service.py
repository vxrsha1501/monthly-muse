"""Generation service: request lifecycle around the pure pipeline (Section 4 + 10.2).

POST returns 202 with request_id; the pipeline runs as a background task; the UI
polls GET /generation-requests/{id} for stage and result.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.arms import arms_table
from app.ai.pipeline import Brief, HistoryItem, PersonalizationState, UI_STAGES
from app.core.errors import Forbidden, NotFound, RateLimited
from app.core.rate_limit import rate_limit
from app.core.runtime import Runtime
from app.db.models import (CycleStatus, GeneratedMessage, GenerationRequest, MessageStatus,
                           MonthlyPlan, Notification, NotificationType, PostCycle, RequestStatus,
                           ToneStat, Topic, TriggerKind, User, UserPreferences)
from app.db.session import SessionLocal, blob_to_vec, vec_to_blob
from app.schemas.generation import CandidateOut, FeatureBreakdown, GenerateIn, NearestPast, StageOut
from app.services.catalog_service import month_name, occasion_suggestions, season_for
from app.services.history_service import content_month

logger = logging.getLogger("monthlymuse.generation")

MAX_HISTORY = 60


# --------------------------------------------------------------- brief

def build_brief(db: Session, user: User, payload: GenerateIn, cycle: PostCycle | None = None) -> Brief:
    prefs = db.get(UserPreferences, user.id) or UserPreferences(user_id=user.id)
    month_int = int(payload.month.split("-")[1])

    topic_name = payload.topic or ""
    topic_keywords: list[str] = []
    if payload.topic_id:
        topic = db.get(Topic, payload.topic_id)
        if topic and (topic.user_id in (None, user.id)):
            topic_name = topic_name or topic.name
            topic_keywords = list(topic.keywords or [])

    audience_name = "our community"
    audience_description = ""
    if payload.audience_id:
        from app.db.models import AudienceProfile
        aud = db.get(AudienceProfile, payload.audience_id)
        if aud and aud.user_id == user.id:
            audience_name = aud.name
            audience_description = aud.description or ""

    # None = not specified -> pull the month's top suggestion (blueprint: "pulls the
    # relevant occasion"); "" = the user explicitly chose None.
    occasion = payload.occasion if payload.occasion is not None else ""
    occasion_fact = ""
    if payload.occasion is None:
        suggestions = occasion_suggestions(db, month_int, user.region, user.id)
        if suggestions:
            occasion = suggestions[0].name
            occasion_fact = suggestions[0].description or ""
    elif occasion:
        from app.db.models import Occasion
        row = db.scalar(select(Occasion).where(Occasion.name == occasion))
        occasion_fact = row.description if row else ""

    # scheduled cycles merge plan defaults with per-cycle overrides (Section 4 step 2)
    plan = None
    if cycle is not None:
        plan = db.get(MonthlyPlan, cycle.plan_id)
    if plan is not None:
        if plan.topic_id and not topic_name:
            plan_topic = db.get(Topic, plan.topic_id)
            topic_name = plan_topic.name if plan_topic else topic_name
        payload.tones = payload.tones or list(plan.tones or ["warm"])
        payload.purpose = payload.purpose or plan.purpose
        payload.platform = payload.platform or plan.platform

    keywords = list(dict.fromkeys([*(payload.keywords or []), *topic_keywords]))[:6]

    return Brief(
        month=payload.month,
        month_name=month_name(month_int),
        topic=topic_name,
        occasion=occasion,
        occasion_fact=occasion_fact,
        season=season_for(month_int, user.region),
        audience=audience_name,
        audience_description=audience_description,
        tones=list(payload.tones or ["warm"]),
        purpose=payload.purpose,
        platform=payload.platform,
        language=payload.language or prefs.default_language,
        length_preset=payload.length or prefs.length_preset,
        keywords=keywords,
        cta=payload.cta or "",
        voice_notes=prefs.voice_notes or "",
        banned_words=list(prefs.banned_words or []),
        emoji_level=prefs.emoji_level,
        additional_instructions=payload.additional_instructions or "",
        lambda_mmr=prefs.variety or 0.7,
    )


def load_history(db: Session, user_id: str, limit: int = MAX_HISTORY) -> list[HistoryItem]:
    """Past selected posts, newest first (the history matrix H)."""
    rows = db.scalars(
        select(GeneratedMessage)
        .where(GeneratedMessage.user_id == user_id,
               GeneratedMessage.status.in_([MessageStatus.SELECTED, MessageStatus.EDITED]),
               GeneratedMessage.embedding.is_not(None))
        .order_by(GeneratedMessage.created_at.desc())
        .limit(limit)
    ).all()
    items: list[HistoryItem] = []
    for row in rows:
        emb = blob_to_vec(row.embedding)
        if emb is not None:
            items.append(HistoryItem(id=row.id, text=row.final_text or row.text, embedding=emb))
    return items


def load_state(db: Session, user_id: str, audience_id: str | None = None) -> PersonalizationState:
    prefs = db.get(UserPreferences, user_id)
    if prefs is None:
        return PersonalizationState()
    from app.ai.scoring import weights_from_dict
    rows = db.scalars(select(ToneStat).where(ToneStat.user_id == user_id)).all()
    arm_stats = arms_table([{"tone": r.tone, "structure": r.structure,
                             "alpha": r.alpha, "beta": r.beta} for r in rows])
    return PersonalizationState(
        preference_vector=blob_to_vec(prefs.preference_vector),
        weights=weights_from_dict(prefs.scoring_weights),
        length_mean=prefs.length_mean,
        length_std=prefs.length_std,
        arm_stats=arm_stats,
    )


# --------------------------------------------------------------- lifecycle

def start_generation(db: Session, user: User, payload: GenerateIn, *,
                     trigger: str = "manual", cycle: PostCycle | None = None,
                     parent_request_id: str | None = None) -> GenerationRequest:
    rate_limit(str(user.id), "generation")
    brief = build_brief(db, user, payload, cycle=cycle)
    req = GenerationRequest(
        user_id=user.id,
        cycle_id=cycle.id if cycle else (payload.cycle_id or None),
        parent_request_id=parent_request_id,
        trigger=trigger if trigger in {t.value for t in TriggerKind} else TriggerKind.MANUAL,
        input={**payload.model_dump(), "_brief_tones": brief.tones,
               "_brief_occasion": brief.occasion, "_brief_topic": brief.topic,
               "_brief_audience": brief.audience, "_brief_season": brief.season},
        status=RequestStatus.QUEUED,
        stage="queued",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    logger.info("generation_queued request=%s trigger=%s", req.id, trigger)
    return req


def run_request(request_id: str) -> None:
    """Background task: runs the pipeline and persists everything (own DB session)."""
    db = SessionLocal()
    started = time.perf_counter()
    try:
        req = db.get(GenerationRequest, request_id)
        if req is None:
            return
        user = db.get(User, req.user_id)
        cycle = db.get(PostCycle, req.cycle_id) if req.cycle_id else None
        if cycle is not None and cycle.status in (CycleStatus.PLANNED, CycleStatus.GENERATION_FAILED,
                                                  CycleStatus.NEEDS_ATTENTION):
            cycle.status = CycleStatus.GENERATING
        else:
            if cycle is not None:
                cycle.status = CycleStatus.GENERATING
        req.status = RequestStatus.RUNNING
        db.commit()

        payload = GenerateIn(**{k: v for k, v in req.input.items() if not k.startswith("_")})
        if req.parent_request_id:
            parent = db.get(GenerationRequest, req.parent_request_id)
            if parent is not None:
                extra = req.input.get("additional_instructions") or ""
                if extra:
                    payload.additional_instructions = extra

        brief = build_brief(db, user, payload, cycle=cycle)
        history = load_history(db, user.id)
        state = load_state(db, user.id, payload.audience_id)

        def stage_cb(stage: str) -> None:
            req.stage = stage
            db.commit()

        runtime = Runtime.init()
        result = runtime.pipeline.run(brief, history, state, stage_cb=stage_cb,
                                      trigger=req.trigger)

        _persist(db, req, user, cycle, result)
        req.status = RequestStatus.DONE
        req.stage = "done"
        req.latency_ms = int((time.perf_counter() - started) * 1000)
        db.commit()
        logger.info("generation_done request=%s n_displayed=%d provider=%s ms=%d",
                    req.id, len(result.displayed), result.request.get("provider"), req.latency_ms)
    except Exception as exc:  # noqa: BLE001 - boundary: mark the request failed
        logger.exception("generation_failed request=%s", request_id)
        try:
            req = db.get(GenerationRequest, request_id)
            if req is not None:
                req.status = RequestStatus.FAILED
                req.error = str(exc)[:500]
                req.stage = "failed"
                if req.cycle_id:
                    cycle = db.get(PostCycle, req.cycle_id)
                    if cycle is not None:
                        cycle.status = CycleStatus.GENERATION_FAILED
                db.commit()
        except Exception:  # pragma: no cover
            logger.exception("could not persist failure")
    finally:
        db.close()


def _persist(db: Session, req: GenerationRequest, user: User, cycle: PostCycle | None, result) -> None:
    req.intent_text = result.request["intent_text"]
    req.intent_embedding = vec_to_blob(result.request["intent_embedding"])
    req.provider = result.request.get("provider")
    req.model = result.request.get("model")
    req.prompt_tokens = result.request.get("prompt_tokens")
    req.completion_tokens = result.request.get("completion_tokens")
    req.cost_usd = result.request.get("cost_usd")
    req.llm_latency_ms = result.request.get("llm_latency_ms")
    req.weights_snapshot = result.request.get("weights_snapshot") or {}

    def make(cand, rank: int | None, status: MessageStatus) -> GeneratedMessage:
        from app.ai.features import FEATURE_NAMES
        feats = {k: cand.features.get(k, 0.0) for k in FEATURE_NAMES}
        explain = dict(cand.explain)
        explain.update({
            "filter_reason": cand.filter_reason,
            "arm": [cand.tone, cand.structure],
            "pca": next(({"x": p["x"], "y": p["y"]} for p in result.embedding_map.get("candidates", [])
                         if p.get("rank") == cand.rank), None),
            "style_label": cand.style_label,
        })
        return GeneratedMessage(
            request_id=req.id, user_id=user.id,
            text=cand.text, final_text=cand.text,
            intended_tone=cand.tone, style_label=cand.style_label,
            word_count=cand.word_count, char_count=cand.char_count,
            embedding=vec_to_blob(cand.embedding),
            f_relevance=feats["relevance"], f_tone=feats["tone"],
            f_personalization=feats["personalization"], f_novelty=feats["novelty"],
            f_length=feats["length"], f_history=feats["history"],
            score=cand.score if cand.status == "shown" else None,
            p_rank1=cand.p_rank1, rank=rank,
            nearest_history_id=cand.nearest_history_id, nearest_cosine=cand.nearest_cosine,
            is_fallback=cand.is_fallback, status=status, explain=explain,
        )

    for cand in result.displayed:
        db.add(make(cand, cand.rank, MessageStatus.SHOWN))
    for cand in result.filtered:
        db.add(make(cand, None, MessageStatus.FILTERED))

    if cycle is not None and result.displayed:
        cycle.status = CycleStatus.AWAITING_REVIEW
        existing = db.scalar(select(Notification).where(
            Notification.cycle_id == cycle.id,
            Notification.type == NotificationType.READY_FOR_REVIEW))
        if existing is None:
            plan = db.get(MonthlyPlan, cycle.plan_id)
            db.add(Notification(
                user_id=user.id, type=NotificationType.READY_FOR_REVIEW,
                title=f"Your {cycle.target_month.strftime('%B')} post is ready for review",
                body=f"3 ranked candidates are waiting in the review screen"
                     + (f" for {plan.name}." if plan else "."),
                cycle_id=cycle.id,
            ))
    db.flush()


# --------------------------------------------------------------- reads

def get_request_stage(db: Session, user_id: str, request_id: str) -> StageOut:
    req = db.get(GenerationRequest, request_id)
    if req is None or req.user_id != user_id:
        raise NotFound("generation request")

    candidates: list[CandidateOut] = []
    arms: list[list[str]] = []
    retrieval: dict = {}
    embedding_map: dict = {"candidates": [], "intent": None, "history": []}
    warnings: list[str] = []

    if req.status == RequestStatus.DONE:
        rows = db.scalars(select(GeneratedMessage).where(GeneratedMessage.request_id == req.id)
                          .order_by(GeneratedMessage.rank.is_(None), GeneratedMessage.rank,
                                    GeneratedMessage.score.desc())).all()
        for row in rows:
            candidates.append(to_candidate_out(row, db, user_id))
            if row.explain.get("arm") and row.explain["arm"] not in arms:
                arms.append(list(row.explain["arm"]))
            if row.explain.get("pca"):
                embedding_map["candidates"].append({"rank": row.rank, **row.explain["pca"]})
        retrieval = {"n_history": len(load_history(db, user_id))}
        warnings = row_warnings(req)

    return StageOut(
        id=req.id, status=req.status.value, stage=req.stage,
        ui_stage=UI_STAGES.get(req.stage or "", None),
        provider=req.provider,
        error=req.error,
        latency_ms=req.latency_ms,
        is_fallback=req.provider == "template",
        candidates=candidates,
        arms=arms,
        retrieval=retrieval,
        embedding_map=embedding_map,
        warnings=warnings,
    )


def row_warnings(req: GenerationRequest) -> list[str]:
    return req.input.get("_warnings") or []


def to_candidate_out(row: GeneratedMessage, db: Session, user_id: str) -> CandidateOut:
    from app.db.models import UserPreferences
    nearest = NearestPast(id=row.nearest_history_id, cosine=row.nearest_cosine,
                          band=(row.explain or {}).get("band", "Fresh"))
    if row.nearest_history_id:
        prev = db.get(GeneratedMessage, row.nearest_history_id)
        if prev is not None and prev.user_id == user_id:
            nearest.preview = (prev.final_text or prev.text)[:140]
            cm = content_month(prev, db=db)
            if cm:
                year, mon = cm.split("-")
                nearest.month = datetime(int(year), int(mon), 1).strftime("%b %Y")
    text = row.final_text or row.text
    return CandidateOut(
        id=row.id, rank=row.rank, text=text,
        style_label=row.style_label, tone=row.intended_tone,
        score=row.score, p_rank1=row.p_rank1,
        confidence=_band(row.p_rank1),
        word_count=row.word_count, char_count=row.char_count,
        is_fallback=row.is_fallback, status=row.status.value,
        filter_reason=(row.explain or {}).get("filter_reason"),
        features=FeatureBreakdown(
            relevance=row.f_relevance or 0.0, tone=row.f_tone or 0.0,
            personalization=row.f_personalization or 0.0, novelty=row.f_novelty or 0.0,
            length=row.f_length or 0.0, history=row.f_history or 0.0,
        ),
        nearest_past=nearest,
        explain={k: v for k, v in (row.explain or {}).items() if k not in ("pca",)},
        selected=row.status == MessageStatus.SELECTED,
        final_text=row.final_text,
    )


def _band(p: float | None) -> str:
    if p is None:
        return "low"
    if p >= 0.60:
        return "high"
    if p >= 0.35:
        return "medium"
    return "low"


def regenerate(db: Session, user: User, request_id: str, instruction: str | None = None) -> GenerationRequest:
    parent = db.get(GenerationRequest, request_id)
    if parent is None or parent.user_id != user.id:
        raise NotFound("generation request")
    payload = GenerateIn(**{k: v for k, v in parent.input.items() if not k.startswith("_")})
    if instruction:
        payload.additional_instructions = instruction
    return start_generation(db, user, payload, trigger=TriggerKind.REGENERATE.value,
                            cycle=db.get(PostCycle, parent.cycle_id) if parent.cycle_id else None,
                            parent_request_id=parent.id)
