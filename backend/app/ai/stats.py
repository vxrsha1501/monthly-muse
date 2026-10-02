"""Statistical primitives (Section 5.2): softmax, Wilson CI, chi-square, entropy,
shrinkage mean, z-scores, Gaussian length suitability."""
from __future__ import annotations

import numpy as np

from app.ai.text import clip01


def softmax(z: np.ndarray, axis: int = -1) -> np.ndarray:
    arr = np.asarray(z, dtype=np.float64)
    shifted = arr - np.max(arr, axis=axis, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=axis, keepdims=True)


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval - behaves well for small samples (Section 5.2)."""
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lo = (centre - margin) / denom
    hi = (centre + margin) / denom
    return (float(clip01(lo)), float(clip01(hi)))


def chi_square_test(table: np.ndarray) -> dict:
    """Chi-square test of independence on an r x c contingency table (tone x selection)."""
    observed = np.asarray(table, dtype=float)
    if observed.shape[0] < 2 or observed.shape[1] < 2:
        return {"chi2": 0.0, "p_value": 1.0, "dof": 0, "insufficient": True}
    if observed.sum() == 0:
        return {"chi2": 0.0, "p_value": 1.0, "dof": (observed.shape[0] - 1) * (observed.shape[1] - 1),
                "insufficient": True}
    row_sums = observed.sum(axis=1, keepdims=True)
    col_sums = observed.sum(axis=0, keepdims=True)
    total = observed.sum()
    expected = row_sums @ col_sums / total
    if np.any(expected < 5):
        # statistical honesty: refuse the significance claim on tiny counts
        chi2 = float(np.sum((observed - expected) ** 2 / np.maximum(expected, 1e-9)))
        return {"chi2": chi2, "p_value": None, "dof": (observed.shape[0] - 1) * (observed.shape[1] - 1),
                "insufficient": True}
    chi2 = float(np.sum((observed - expected) ** 2 / expected))
    dof = (observed.shape[0] - 1) * (observed.shape[1] - 1)
    try:
        from scipy.stats import chi2 as chi2_dist
        p_value = float(1 - chi2_dist.cdf(chi2, dof))
    except Exception:  # pragma: no cover
        p_value = None
    return {"chi2": chi2, "p_value": p_value, "dof": dof, "insufficient": False}


def shannon_entropy(counts: list[int] | np.ndarray, k: int | None = None) -> float:
    """Normalised Shannon diversity index H / log K in [0, 1]."""
    arr = np.asarray(counts, dtype=float)
    total = arr.sum()
    if total <= 0:
        return 0.0
    K = k or len(arr)
    if K <= 1:
        return 0.0
    p = arr / total
    p = p[p > 0]
    H = float(-np.sum(p * np.log(p)))
    return float(clip01(H / np.log(K)))


def shrink_mean(n: int, xbar: float, k: int = 3, mu0: float | None = None) -> float:
    """Shrinkage toward the preset prior: mu' = (n*xbar + k*mu0)/(n + k) (Section 5.2)."""
    if n <= 0:
        return float(xbar if mu0 is None else mu0)
    if mu0 is None:
        return float(xbar)
    return float((n * xbar + k * mu0) / (n + k))


def zscore(value: float, mean: float, sd: float) -> float:
    if sd is None or sd <= 1e-9:
        return 0.0
    return float((value - mean) / sd)


def length_suitability(length: int, mean: float, std: float | None = None) -> float:
    """Gaussian bump around the preferred length; sigma = max(sigma_user, 0.15*mean)."""
    if not mean or mean <= 0:
        return 1.0
    sigma = max(float(std or 0.0), 0.15 * mean)
    return float(np.exp(-((length - mean) ** 2) / (2 * sigma * sigma)))


def mean_pairwise_cosine(matrix: np.ndarray) -> float:
    """(2/(r(r-1))) * sum_{i<j} cos(e_i, e_j) over the last r selected posts."""
    X = np.asarray(matrix, dtype=np.float32)
    r = X.shape[0]
    if r < 2:
        return 0.0
    S = X @ X.T
    iu = np.triu_indices(r, k=1)
    return float(np.mean(S[iu]))


def point_biserial(scores: np.ndarray, selected: np.ndarray) -> float:
    """Correlation between score and selection (0/1) - scorer validity metric."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(selected, dtype=float)
    if s.size < 3 or np.std(s) == 0 or np.std(y) == 0:
        return 0.0
    return float(np.corrcoef(s, y)[0, 1])


def mean_reciprocal_rank(ranks_of_chosen: list[int]) -> float:
    if not ranks_of_chosen:
        return 0.0
    return float(np.mean([1.0 / r for r in ranks_of_chosen if r > 0]))
