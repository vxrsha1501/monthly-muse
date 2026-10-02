"""Feedback -> personalization updates (Section 7). Pure functions, no DB imports.

Rewards (Section 7.3):
    select +1.0 | edit-then-select +0.8 | copy +0.6 | like +0.5 | dislike -0.5
    reject -0.8 (beta +2, reason weight nudge) | regenerate -0.3 per shown
    ignore 0 (counts as shown; beta +1 for non-selected arms once resolved)
"""
from __future__ import annotations

import numpy as np

from app.ai.similarity import ema_update
from app.ai.stats import shrink_mean
from app.ai.text import edit_distance_tokens

REWARDS: dict[str, float] = {
    "select": 1.0,
    "edit_then_select": 0.8,
    "copy": 0.6,
    "like": 0.5,
    "dislike": -0.5,
    "reject": -0.8,
    "regenerate": -0.3,
    "ignore": 0.0,
}

LENGTH_WINDOW = 10
SHRINK_K = 3


def reward_for(event_type: str, *, edited: bool = False, n_shown: int = 1) -> float:
    if event_type == "select" and edited:
        return REWARDS["edit_then_select"]
    r = REWARDS.get(event_type, 0.0)
    if event_type == "regenerate":
        return r  # applied per shown candidate by the caller
    return r


def update_preference(u: np.ndarray | None, e: np.ndarray | None, reward: float, *,
                      paused: bool = False, alpha: float = 0.95, eta: float = 0.15) -> np.ndarray | None:
    """u <- normalize(alpha*u + eta*reward*e); skipped when learning is paused."""
    if paused or e is None or reward == 0.0:
        return u
    return ema_update(u, e, alpha=alpha, eta=eta, reward=reward)


def update_arm(alpha: float, beta: float, outcome: str) -> tuple[float, float]:
    if outcome == "select":
        return alpha + 1.0, beta
    if outcome == "reject":
        return alpha, beta + 2.0
    if outcome == "like":
        return alpha + 0.5, beta
    if outcome == "dislike":
        return alpha, beta + 0.5
    if outcome == "shown":            # shown, not selected, batch resolved
        return alpha, beta + 1.0
    if outcome == "regenerate":
        return alpha, beta + 1.0
    return alpha, beta


def update_length_stats(window: list[int], new_count: int, preset_mean: float | None = None) -> tuple[float, float, int]:
    """Mean/SD of the last 10 selected word counts, shrunk toward the preset (Section 5.2)."""
    values = [v for v in (window + [new_count]) if v and v > 0][-LENGTH_WINDOW:]
    if not values:
        return (float(preset_mean or 45.0), 0.0, 0)
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if len(values) > 1 else 0.0
    if preset_mean:
        mean = shrink_mean(len(values), mean, k=SHRINK_K, mu0=preset_mean)
    return mean, std, len(values)


def normalised_edit_distance(original: str, final: str) -> float:
    if not original or not final:
        return 0.0
    d = edit_distance_tokens(original, final)
    denom = max(len(original.split()), len(final.split()), 1)
    return min(1.0, d / denom)


def tone_count_key(audience_id: str | None) -> str:
    return audience_id or "__all__"
