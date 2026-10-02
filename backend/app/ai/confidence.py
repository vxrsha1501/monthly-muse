"""Ranking confidence by Monte-Carlo: Dirichlet-perturbed weights (Section 5.1.4).

    w* ~ Dirichlet(alpha * w),  alpha = 50,  s = 1..200
    P(rank 1 | c) ~= (1/S) * #{ s : c has the highest score under w*_s }

High >= 0.60, Medium 0.35-0.60, Low < 0.35. Seeded RNG so results are reproducible.
"""
from __future__ import annotations

import numpy as np

ALPHA = 50.0
SAMPLES = 200


def rank1_confidence(F: np.ndarray, w: np.ndarray, samples: int = SAMPLES, alpha: float = ALPHA,
                     seed: int = 42) -> np.ndarray:
    """Share of perturbed weight vectors under which each candidate ranks first."""
    F = np.asarray(F, dtype=np.float64)
    w = np.asarray(w, dtype=np.float64)
    n = F.shape[0]
    if n == 0:
        return np.zeros(0)
    if n == 1:
        return np.array([1.0])
    rng = np.random.default_rng(seed)
    concentration = np.clip(alpha * w, 1e-3, None)
    wins = np.zeros(n, dtype=np.int64)
    for _ in range(samples):
        w_star = rng.dirichlet(concentration)
        s = F @ w_star
        wins[int(np.argmax(s))] += 1
    return wins / samples


def confidence_band(p_rank1: float) -> str:
    if p_rank1 >= 0.60:
        return "high"
    if p_rank1 >= 0.35:
        return "medium"
    return "low"
