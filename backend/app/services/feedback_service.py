"""Feedback service: every action becomes a learning signal (Section 7).

Writes append-only feedback_events, then updates (unless learning is paused):
preference vector u, Beta arm posteriors, length statistics, tone-by-audience
counts and the scoring weights (projected gradient step on the batch).
"""
from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.arms import arm_catalog, style_label
from app.ai.embedder import get_embedder
from app.ai.personalization import (REWARDS, normalised_edit_distance, reward_for,
                                    update_arm, update_length_stats, update_preference)
from app.ai.scoring import DEFAULT_WEIGHTS, gradient_step, reason_nudge, weights_from_dict, weights_to_dict
from app.core.config import settings
from app.core.errors import NotFound
from app.db.models import (FeedbackEvent, FeedbackType, GeneratedMessage, GenerationRequest,
                           MessageStatus, PostCycle, ToneStat, User, UserPreferences)
from app.db.session import blob_to_vec, vec_to_blob

logger = logging.getLogger("monthlymuse.feedback")

FEATURE_COLS = ("f_relevance", "f_tone", "f_personalization", "f_novelty", "f_length", "f_history")


def _owned_message(db: Session, user: User, message_id: str) -> GeneratedMessage:
    row = db.get(GeneratedMessage, message_id)
    if row is None or row.user_id != user.id:
        raise NotFound("message")
    return row


def record_event(db: Session, user: User, message: GeneratedMessage | None, *,
                 event_type: str, reward: float = 0.0, reason: str | None = None,
                 edit_distance: int | None = None, processed: bool = True) -> FeedbackEvent:
    event = FeedbackEvent(
        user_id=user.id,
        message_id=message.id if message else None,
        request_id=message.request_id if message else None,
        event_type=FeedbackType(event_type),
        reward=reward, reason=reason, edit_distance=edit_distance, processed=processed,
    )
    db.add(event)
    return event


def _embed(text: str) -> np.ndarray | None:
    if not text:
        return None
    try:
        emb = get_embedder(settings.embedder, settings.embedding_dim, settings.embedding_model).embed([text])[0]
        return emb
    except Exception:  # pragma: no cover
        return None


def _get_or_create_arm(db: Session, user_id: str, tone: str, structure: str) -> ToneStat:
    row = db.scalar(select(ToneStat).where(ToneStat.user_id == user_id, ToneStat.tone == tone,
                                           ToneStat.structure == structure,
                                           ToneStat.audience_id.is_(None)))
    if row is None:
        row = ToneStat(user_id=user_id, audience_id=None, tone=tone, structure=structure,
                       alpha=1.0, beta=1.0)
        db.add(row)
        db.flush()
    return row


def _batch(db: Session, request_id: str) -> list[GeneratedMessage]:
    return list(db.scalars(
        select(GeneratedMessage).where(GeneratedMessage.request_id == request_id,
                                       GeneratedMessage.rank.is_not(None))
        .order_by(GeneratedMessage.rank)).all())


# --------------------------------------------------------------- edit

def handle_edit(db: Session, user: User, message_id: str, final_text: str) -> GeneratedMessage:
    message = _owned_message(db, user, message_id)
    original = message.final_text or message.text
    distance = normalised_edit_distance(original, final_text)
    message.final_text = final_text
    message.word_count = len(final_text.split())
    message.char_count = len(final_text)
    emb = _embed(final_text)
    if emb is not None:
        message.embedding = vec_to_blob(emb)
    if message.status == MessageStatus.SHOWN:
        message.status = MessageStatus.EDITED
    record_event(db, user, message, event_type="edit", reward=0.0,
                 edit_distance=int(round(distance * 100)))
    db.commit()
    db.refresh(message)
    logger.info("message_edited message=%s distance=%.2f", message.id, distance)
    return message


# --------------------------------------------------------------- select

def handle_select(db: Session, user: User, message_id: str) -> GeneratedMessage:
    message = _owned_message(db, user, message_id)
    request = db.get(GenerationRequest, message.request_id)
    prefs = db.get(UserPreferences, user.id) or UserPreferences(user_id=user.id)
    paused = bool(prefs.learning_paused)

    siblings = _batch(db, message.request_id) or [message]
    edited = message.status == MessageStatus.EDITED or (
        message.final_text and message.final_text != message.text)
    reward = reward_for("select", edited=bool(edited))

    # --- preference vector (toward the user's true target: the final text)
    target = message.final_text or message.text
    emb = blob_to_vec(message.embedding)
    if emb is None:
        emb = _embed(target)
    if not paused and emb is not None:
        new_u = update_preference(blob_to_vec(prefs.preference_vector), emb, reward)
        if new_u is not None:
            prefs.preference_vector = vec_to_blob(new_u)

    # --- scoring weights: one projected gradient step on the displayed batch
    if not paused and len(siblings) >= 2:
        F = np.array([[getattr(s, c) or 0.0 for c in FEATURE_COLS] for s in siblings], dtype=float)
        chosen_idx = next((i for i, s in enumerate(siblings) if s.id == message.id), 0)
        w = weights_from_dict(prefs.scoring_weights)
        prefs.scoring_weights = weights_to_dict(gradient_step(F, chosen_idx, w))

    # --- Beta arm posteriors (Section 5.1.2)
    chosen_tone = message.intended_tone or "warm"
    chosen_structure = (message.explain or {}).get("arm", [chosen_tone, "story-opening"])
    chosen_structure = chosen_structure[1] if isinstance(chosen_structure, list) else "story-opening"
    if not paused:
        arm = _get_or_create_arm(db, user.id, chosen_tone, chosen_structure)
        arm.alpha, arm.beta = update_arm(arm.alpha, arm.beta, "select")
        arm.n_selected += 1
        seen_arms: set[tuple[str, str]] = set()
        for s in siblings:
            if s.id == message.id:
                continue
            s_tone = s.intended_tone or "warm"
            s_arm = (s.explain or {}).get("arm", [s_tone, "story-opening"])
            s_structure = s_arm[1] if isinstance(s_arm, list) else "story-opening"
            key = (s_tone, s_structure)
            if key in seen_arms:
                continue
            seen_arms.add(key)
            other = _get_or_create_arm(db, user.id, s_tone, s_structure)
            other.alpha, other.beta = update_arm(other.alpha, other.beta, "shown")
            other.n_shown += 1
        arm.n_shown += 1
        for s in siblings:
            if s.id != message.id:
                record_event(db, user, s, event_type="ignore", reward=REWARDS["ignore"], processed=True)

    # --- length statistics from the final (post-edit) word count
    if not paused:
        window = [r.word_count for r in db.scalars(
            select(GeneratedMessage).where(GeneratedMessage.user_id == user.id,
                                           GeneratedMessage.status == MessageStatus.SELECTED,
                                           GeneratedMessage.id != message.id)
            .order_by(GeneratedMessage.created_at.desc()).limit(9)).all() if r.word_count]
        from app.ai.limits import preset_range
        preset_mid = sum(preset_range(prefs.length_preset or "medium")) / 2
        mean, std, n = update_length_stats(window, message.word_count or 0, preset_mid)
        prefs.length_mean, prefs.length_std, prefs.length_n = mean, std, n

    # --- tone-by-audience counts (Section 5.1.3)
    if not paused:
        audience_key = (request.input.get("audience_id") if request else None) or "__all__"
        counts = dict(prefs.tone_counts or {})
        inner = dict(counts.get(audience_key) or {})
        inner[chosen_tone] = int(inner.get(chosen_tone, 0)) + 1
        counts[audience_key] = inner
        prefs.tone_counts = counts

    # --- statuses, cycle link and events
    message.status = MessageStatus.SELECTED
    if request is not None and request.cycle_id:
        cycle = db.get(PostCycle, request.cycle_id)
        if cycle is not None:
            cycle.selected_message_id = message.id
    if edited:
        record_event(db, user, message, event_type="edit", reward=0.0,
                     edit_distance=int(round(normalised_edit_distance(message.text, target) * 100)))
    record_event(db, user, message, event_type="select", reward=reward, processed=True)
    db.commit()
    db.refresh(message)
    logger.info("message_selected message=%s reward=%.1f edited=%s", message.id, reward, bool(edited))
    return message


# --------------------------------------------------------------- other feedback

def handle_feedback(db: Session, user: User, message_id: str, event_type: str,
                    reason: str | None = None) -> dict:
    message = _owned_message(db, user, message_id)
    prefs = db.get(UserPreferences, user.id) or UserPreferences(user_id=user.id)
    paused = bool(prefs.learning_paused)

    tone = message.intended_tone or "warm"
    arm_meta = (message.explain or {}).get("arm", [tone, "story-opening"])
    structure = arm_meta[1] if isinstance(arm_meta, list) else "story-opening"

    if event_type == "reject":
        reward = REWARDS["reject"]
        if not paused:
            arm = _get_or_create_arm(db, user.id, tone, structure)
            arm.alpha, arm.beta = update_arm(arm.alpha, arm.beta, "reject")
            if message.status in (MessageStatus.SHOWN, MessageStatus.EDITED):
                message.status = MessageStatus.REJECTED
            prefs.scoring_weights = weights_to_dict(
                reason_nudge(weights_from_dict(prefs.scoring_weights), reason or ""))
    elif event_type == "like":
        reward = REWARDS["like"]
        if not paused:
            arm = _get_or_create_arm(db, user.id, tone, structure)
            arm.alpha, arm.beta = update_arm(arm.alpha, arm.beta, "like")
    elif event_type == "dislike":
        reward = REWARDS["dislike"]
        if not paused:
            arm = _get_or_create_arm(db, user.id, tone, structure)
            arm.alpha, arm.beta = update_arm(arm.alpha, arm.beta, "dislike")
    elif event_type == "copy":
        reward = REWARDS["copy"]
        if not paused:
            arm = _get_or_create_arm(db, user.id, tone, structure)
            arm.alpha, arm.beta = update_arm(arm.alpha, arm.beta, "select")
            arm.n_selected += 0  # counts as a weak positive without a selection
    elif event_type == "regenerate":
        reward = REWARDS["regenerate"]
        if not paused:
            arm = _get_or_create_arm(db, user.id, tone, structure)
            arm.alpha, arm.beta = update_arm(arm.alpha, arm.beta, "regenerate")
    elif event_type == "ignore":
        reward = REWARDS["ignore"]
        if not paused:
            arm = _get_or_create_arm(db, user.id, tone, structure)
            arm.alpha, arm.beta = update_arm(arm.alpha, arm.beta, "shown")
            arm.n_shown += 1
    else:
        reward = REWARDS.get(event_type, 0.0)

    # preference vector moves with positive/negative signals
    if not paused and reward != 0.0:
        emb = blob_to_vec(message.embedding)
        if emb is None:
            emb = _embed(message.final_text or message.text)
        new_u = update_preference(blob_to_vec(prefs.preference_vector), emb, reward)
        if new_u is not None:
            prefs.preference_vector = vec_to_blob(new_u)

    record_event(db, user, message, event_type=event_type, reward=reward, reason=reason, processed=True)
    db.commit()
    return {"event_type": event_type, "reward": reward, "reason": reason,
            "weights": weights_to_dict(weights_from_dict(prefs.scoring_weights))}


def resolve_ignored(db: Session, user: User, request_id: str, exclude_message_id: str) -> None:
    """Log ignore events for shown-but-not-selected candidates of a batch."""
    for row in _batch(db, request_id):
        if row.id == exclude_message_id or row.status in (MessageStatus.SELECTED, MessageStatus.REJECTED):
            continue
        record_event(db, user, row, event_type="ignore", reward=REWARDS["ignore"], processed=True)
    db.commit()
