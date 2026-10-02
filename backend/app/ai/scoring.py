"""Weighted scoring, weight constraints and learning by gradient descent (Section 5.6).

    S(c) = F . w        subject to sum w = 1, 0.02 <= w_k <= 0.50
    P(c | batch) = softmax(beta * s_c)
    grad = -beta (f_chosen - sum_k P(k) f_k) + 2 * lam * (w - w0)
"""
from __future__ import annotations

import numpy as np

from app.ai.features import FEATURE_KEYS, FEATURE_NAMES
from app.ai.stats import softmax

DEFAULT_WEIGHTS = np.array([0.30, 0.20, 0.20, 0.15, 0.10, 0.05], dtype=np.float64)  # R T P N Len Hist
WEIGHT_MIN, WEIGHT_MAX = 0.02, 0.50
CHOICE_BETA = 8.0
LEARNING_RATE = 0.01
REGULARISATION = 0.005


def weights_from_dict(values: dict | None) -> np.ndarray:
    if not values:
        return DEFAULT_WEIGHTS.copy()
    arr = np.array([float(values.get(k, DEFAULT_WEIGHTS[i])) for i, k in enumerate(FEATURE_KEYS)],
                   dtype=np.float64)
    return sanitize_weights(arr)


def weights_to_dict(w: np.ndarray) -> dict[str, float]:
    return {k: float(v) for k, v in zip(FEATURE_KEYS, w)}


def sanitize_weights(w: np.ndarray) -> np.ndarray:
    """Project onto the allowed set: clip to [0.02, 0.50], renormalise to sum 1.

    Clipping and renormalising fight each other, so iterate until both hold
    (converges in a handful of rounds for any input).
    """
    arr = np.asarray(w, dtype=np.float64)
    if not np.all(np.isfinite(arr)) or arr.sum() <= 0:
        return DEFAULT_WEIGHTS.copy()
    arr = np.clip(arr, WEIGHT_MIN, WEIGHT_MAX)
    for _ in range(50):
        arr = arr / arr.sum()
        if arr.max() <= WEIGHT_MAX + 1e-12 and arr.min() >= WEIGHT_MIN - 1e-12:
            break
        arr = np.clip(arr, WEIGHT_MIN, WEIGHT_MAX)
    return arr / arr.sum()


def scores(F: np.ndarray, w: np.ndarray) -> np.ndarray:
    """s = F . w for the whole batch in one matrix-vector product."""
    return np.asarray(F, dtype=np.float64) @ np.asarray(w, dtype=np.float64)


def choice_probabilities(F: np.ndarray, w: np.ndarray, beta: float = CHOICE_BETA) -> np.ndarray:
    """Softmax over batch scores: P(c | batch)."""
    return softmax(beta * scores(F, w))


def gradient_step(F: np.ndarray, chosen_idx: int, w: np.ndarray, *,
                  w0: np.ndarray | None = None, lr: float = LEARNING_RATE,
                  lam: float = REGULARISATION, beta: float = CHOICE_BETA) -> np.ndarray:
    """One projected gradient step on the listwise softmax loss (Section 5.6.3)."""
    w = np.asarray(w, dtype=np.float64)
    w0 = DEFAULT_WEIGHTS.copy() if w0 is None else np.asarray(w0, dtype=np.float64)
    F = np.asarray(F, dtype=np.float64)
    probs = softmax(beta * scores(F, w))
    expected = probs @ F                      # batch's expected feature vector
    grad = -beta * (F[chosen_idx] - expected) + 2.0 * lam * (w - w0)
    return sanitize_weights(w - lr * grad)


def fit_weights(batches: list[tuple[np.ndarray, int]], w0: np.ndarray | None = None, *,
                epochs: int = 1, lr: float = LEARNING_RATE, lam: float = REGULARISATION,
                beta: float = CHOICE_BETA) -> np.ndarray:
    """Re-fit from full history (weekly stabilisation run)."""
    w = (DEFAULT_WEIGHTS.copy() if w0 is None else np.asarray(w0, dtype=np.float64).copy())
    for _ in range(epochs):
        for F, chosen in batches:
            w = gradient_step(F, chosen, w, w0=DEFAULT_WEIGHTS, lr=lr, lam=lam, beta=beta)
    return w


def reason_nudge(w: np.ndarray, reason: str) -> np.ndarray:
    """Reject-reason chips nudge specific weights (Section 3.3 / 7.3)."""
    nudges = {
        "too_similar": 3,   # raise novelty
        "off_topic": 0,     # raise relevance
        "too_long": 4,      # raise length sensitivity
        "too_short": 4,
        "too_formal": 1,    # lower tone match pressure
        "too_casual": 1,
        "wrong_facts": 0,
    }
    idx = nudges.get(reason or "")
    arr = np.asarray(w, dtype=np.float64).copy()
    if idx is None:
        return arr
    arr[idx] *= 1.05
    return sanitize_weights(arr)


__all__ = [
    "DEFAULT_WEIGHTS", "FEATURE_KEYS", "FEATURE_NAMES", "WEIGHT_MIN", "WEIGHT_MAX",
    "weights_from_dict", "weights_to_dict", "sanitize_weights", "scores",
    "choice_probabilities", "gradient_step", "fit_weights", "reason_nudge",
]
