"""The six scoring features f(c) in [0, 1] (Section 5.6.1)."""
from __future__ import annotations

import numpy as np

from app.ai.similarity import preference_match
from app.ai.stats import length_suitability
from app.ai.text import clip01, rescale

FEATURE_NAMES = ("relevance", "tone", "personalization", "novelty", "length", "history")
FEATURE_KEYS = ("R", "T", "P", "N", "Len", "Hist")


def relevance_feature(cos_intent: float, keyword_cov: float, a: float = 0.20, b: float = 0.80) -> float:
    """0.7 * rescaled cosine to intent + 0.3 * keyword coverage."""
    return float(clip01(0.7 * rescale(cos_intent, a, b) + 0.3 * clip01(keyword_cov)))


def tone_feature(p_intended: float) -> float:
    """P(intended tone | text) from the softmax classifier; 0.5-ish neutral if unknown."""
    return float(clip01(p_intended))


def personalization_feature(e: np.ndarray, u: np.ndarray | None) -> float:
    """(1 + cos(e, u)) / 2."""
    return float(preference_match(e, u))


def novelty_feature(max_cos_history: float) -> float:
    """clip(1 - max_j cos(e, h_j), 0, 1); 1 = nothing like the past."""
    return float(clip01(1.0 - max_cos_history))


def length_feature(word_count: int, mean: float, std: float | None = None) -> float:
    return float(length_suitability(word_count, mean, std))


def history_feature(alpha: float, beta: float) -> float:
    """Posterior mean of the arm's Beta distribution; 0.5 with no data."""
    if alpha <= 0 or (alpha + beta) <= 0:
        return 0.5
    return float(alpha / (alpha + beta))


def build_feature_row(*, cos_intent: float, keyword_cov: float, p_intended: float,
                      e: np.ndarray, u: np.ndarray | None, max_cos_history: float,
                      word_count: int, length_mean: float, length_std: float | None,
                      alpha: float, beta: float) -> np.ndarray:
    return np.array([
        relevance_feature(cos_intent, keyword_cov),
        tone_feature(p_intended),
        personalization_feature(e, u),
        novelty_feature(max_cos_history),
        length_feature(word_count, length_mean, length_std),
        history_feature(alpha, beta),
    ], dtype=np.float64)


def feature_dict(row: np.ndarray) -> dict[str, float]:
    return {name: float(v) for name, v in zip(FEATURE_NAMES, row)}
