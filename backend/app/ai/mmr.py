"""Maximal Marginal Relevance: greedy subset optimisation for the final top-3 (Section 5.6.2).

    c* = argmax_{c not in Sel} [ lambda * S(c) - (1 - lambda) * max_{s in Sel} cos(e_c, e_s) ]

Also exposes the exhaustive 20-subset objective so tests can prove greedy == optimal
for n=6, k=3.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np


def mmr_select(F_scores: np.ndarray, embeddings: np.ndarray, k: int = 3, lam: float = 0.7,
                groups: list[int] | None = None) -> list[int]:
    """Greedy MMR. Returns the selection order (first pick = highest score).

    When ``groups`` (one entry per candidate, e.g. the style arm index) is given,
    the greedy step prefers candidates from groups not yet represented, so the
    three displayed messages carry three different style labels (US-11). It only
    repeats a group when nothing else remains.
    """
    scores = np.asarray(F_scores, dtype=np.float64)
    n = len(scores)
    k = min(k, n)
    if n == 0:
        return []
    E = np.asarray(embeddings, dtype=np.float32)
    if E.shape[0] != n:
        raise ValueError("embeddings and scores must align")

    selected: list[int] = [int(np.argmax(scores))]
    remaining = set(range(n)) - set(selected)
    while len(selected) < k and remaining:
        pool = sorted(remaining)
        if groups is not None:
            used = {groups[i] for i in selected}
            fresh = [i for i in pool if groups[i] not in used]
            if fresh:
                pool = fresh
        best_idx, best_val = None, -np.inf
        for idx in pool:
            max_sim = max(float(np.dot(E[idx], E[s])) for s in selected)  # rows are L2-normalised
            value = lam * scores[idx] - (1.0 - lam) * max_sim
            if value > best_val:
                best_val, best_idx = value, idx
        assert best_idx is not None
        selected.append(best_idx)
        remaining.discard(best_idx)
    return selected


def subset_objective(subset: tuple[int, ...] | list[int], scores: np.ndarray, S_cc: np.ndarray,
                     lam: float = 0.7) -> float:
    """sum S(c) - (1 - lambda) * sum_{i<j} cos(e_i, e_j)."""
    s = float(sum(scores[i] for i in subset))
    penalty = 0.0
    for i, j in combinations(subset, 2):
        penalty += float(S_cc[i, j])
    return s - (1.0 - lam) * penalty


def exhaustive_best(scores: np.ndarray, S_cc: np.ndarray, k: int = 3, lam: float = 0.7) -> tuple[list[int], float]:
    """Optimal subset by brute force - feasible because C(6,3) = 20."""
    n = len(scores)
    best_subset: list[int] = list(range(min(k, n)))
    best_val = -np.inf
    for combo in combinations(range(n), k):
        val = subset_objective(combo, scores, S_cc, lam)
        if val > best_val:
            best_val, best_subset = val, list(combo)
    return best_subset, best_val
