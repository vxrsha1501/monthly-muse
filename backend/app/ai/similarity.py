"""Vectors, cosine similarity and matrix products (Sections 5.3-5.5)."""
from __future__ import annotations

import numpy as np

from app.ai.text import clip01


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    arr = np.atleast_2d(np.asarray(matrix, dtype=np.float32))
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def similarity_matrix(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Cosine similarity between every row of A and every row of B: (n x d)(d x m) -> n x m."""
    if A.size == 0 or B.size == 0:
        return np.zeros((A.shape[0] if A.ndim == 2 else 0, B.shape[0] if B.ndim == 2 else 0), dtype=np.float32)
    return l2_normalize(A) @ l2_normalize(B).T


def max_similarity_to_history(E: np.ndarray, H: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Row-wise max cosine of E against H. Returns (max_cos, argmax_idx); empty H -> (0, -1)."""
    if H.size == 0:
        return np.zeros(E.shape[0], dtype=np.float32), np.full(E.shape[0], -1, dtype=int)
    S = similarity_matrix(E, H)
    idx = np.argmax(S, axis=1)
    return S[np.arange(S.shape[0]), idx], idx


def novelty_scores(E: np.ndarray, H: np.ndarray) -> np.ndarray:
    """N(c) = clip(1 - max_j cos(e_c, h_j), 0, 1); 1.0 when there is no history."""
    max_cos, _ = max_similarity_to_history(E, H)
    return np.clip(1.0 - max_cos, 0.0, 1.0)


def top_k_similar(query: np.ndarray, H: np.ndarray, k: int) -> list[int]:
    """Indices of H most similar to query, descending."""
    if H.size == 0:
        return []
    sims = similarity_matrix(np.asarray(query, dtype=np.float32).reshape(1, -1), H)[0]
    order = np.argsort(-sims)
    return [int(i) for i in order[: min(k, len(order))]]


def preference_match(e: np.ndarray, u: np.ndarray | None) -> float:
    """P(c) = (1 + cos(e, u)) / 2; neutral 0.5 with no preference vector."""
    if u is None:
        return 0.5
    return clip01((1.0 + cosine(e, u)) / 2.0)


def ema_update(old: np.ndarray | None, new: np.ndarray, alpha: float = 0.95, eta: float = 0.15,
               reward: float = 1.0) -> np.ndarray:
    """Preference vector update: u <- normalize(alpha*u + eta*reward*e) (Section 7.2)."""
    new = np.asarray(new, dtype=np.float32).ravel()
    if old is None:
        base = np.zeros_like(new)
    else:
        base = np.asarray(old, dtype=np.float32).ravel()
    blended = alpha * base + eta * float(reward) * new
    return l2_normalize(blended.reshape(1, -1))[0]


def cosine_bands(max_cos: float) -> str:
    """UI labels from the (calibratable) cosine bands of Section 5.5.3."""
    if max_cos < 0.50:
        return "Fresh"
    if max_cos < 0.75:
        return "Related"
    if max_cos < 0.85:
        return "Similar"
    return "Repetitive"


def pca_2d(matrix: np.ndarray) -> np.ndarray:
    """SVD/PCA projection of the centred embedding matrix to 2-D for the embedding map."""
    if matrix.size == 0:
        return np.zeros((0, 2), dtype=np.float32)
    X = np.asarray(matrix, dtype=np.float32)
    if X.shape[0] == 1:
        return np.zeros((1, 2), dtype=np.float32)
    centred = X - X.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    return centred @ vt[:2].T
