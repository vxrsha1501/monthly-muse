"""Style arms and Thompson sampling (Section 5.1.2).

An arm is a pair (tone, structure), e.g. (warm, story-opening). Every user keeps a
Beta posterior over "probability I select a message from this arm when shown".
Priors: Beta(1,1); alpha=2 for tones ticked at onboarding. Updates:
select -> alpha+1; shown not selected -> beta+1; rejected -> beta+2.
"""
from __future__ import annotations

import numpy as np

TONES = ("warm", "playful", "professional", "inspirational", "witty", "grateful", "urgent", "formal")

STRUCTURES = ("story-opening", "punchy", "question-hook", "gratitude-first", "announcement", "personal-note")

PURPOSES = ("inform", "promote", "celebrate", "thank", "engage", "announce")

TONE_LABELS = {
    "warm": "Warm", "playful": "Playful", "professional": "Professional",
    "inspirational": "Inspirational", "witty": "Witty", "grateful": "Grateful",
    "urgent": "Urgent", "formal": "Formal",
}
STRUCTURE_LABELS = {
    "story-opening": "Story opening", "punchy": "Punchy", "question-hook": "Question hook",
    "gratitude-first": "Gratitude first", "announcement": "Announcement", "personal-note": "Personal note",
}


def arm_catalog(tones: tuple[str, ...] = TONES, structures: tuple[str, ...] = STRUCTURES) -> list[tuple[str, str]]:
    return [(t, s) for t in tones for s in structures]


def style_label(tone: str, structure: str) -> str:
    return f"{TONE_LABELS.get(tone, tone.title())} - {STRUCTURE_LABELS.get(structure, structure.title())}"


def adjacent_tones(requested: list[str], all_tones: tuple[str, ...] = TONES) -> list[str]:
    """Tones adjacent to the requested ones in the catalogue order (exploration pool)."""
    order = list(all_tones)
    pool: list[str] = []
    for tone in requested:
        if tone not in order:
            continue
        i = order.index(tone)
        pool.extend([order[(i - 1) % len(order)], order[(i + 1) % len(order)]])
    seen: list[str] = []
    for t in pool:
        if t not in requested and t not in seen:
            seen.append(t)
    return seen or [t for t in order if t not in requested]


def sample_arm_value(alpha: float, beta: float, rng: np.random.Generator) -> float:
    return float(rng.beta(max(alpha, 1e-3), max(beta, 1e-3)))


def thompson_select(requested_tones: list[str], arm_stats: dict[tuple[str, str], tuple[float, float]], *,
                    rng: np.random.Generator, n_arms: int = 3) -> list[tuple[str, str]]:
    """Pick n_arms arms: two use the requested tone(s) with different structures, the third explores.

    Returns [(tone, structure), ...].
    """
    requested = [t for t in (requested_tones or []) if t] or ["warm"]
    chosen: list[tuple[str, str]] = []

    # 1) exploitation arms: requested tones, best-sampled structure each
    for tone in requested[:2]:
        best_struct, best_val = None, -1.0
        for structure in STRUCTURES:
            a, b = arm_stats.get((tone, structure), (1.0, 1.0))
            val = sample_arm_value(a, b, rng)
            if val > best_val:
                best_val, best_struct = val, structure
        if best_struct:
            chosen.append((tone, best_struct))

    # 2) exploration arm: sample candidate arms from adjacent tones, take the highest draw
    if len(chosen) < n_arms:
        pool = adjacent_tones(requested)
        best_arm, best_val = None, -1.0
        for tone in pool:
            for structure in STRUCTURES:
                a, b = arm_stats.get((tone, structure), (1.0, 1.0))
                val = sample_arm_value(a, b, rng)
                if val > best_val:
                    best_val, best_arm = val, (tone, structure)
        if best_arm and best_arm not in chosen:
            chosen.append(best_arm)

    # 3) guarantee n_arms distinct arms
    for tone in TONES:
        if len(chosen) >= n_arms:
            break
        for structure in STRUCTURES:
            if (tone, structure) not in chosen:
                chosen.append((tone, structure))
                break
    return chosen[:n_arms]


def beta_update(alpha: float, beta: float, outcome: str) -> tuple[float, float]:
    if outcome == "select":
        return alpha + 1, beta
    if outcome == "ignore":
        return alpha, beta + 1
    if outcome == "reject":
        return alpha, beta + 2
    return alpha, beta


def arms_table(stats_rows: list[dict]) -> dict[tuple[str, str], tuple[float, float]]:
    return {(r["tone"], r["structure"]): (float(r.get("alpha", 1.0)), float(r.get("beta", 1.0)))
            for r in stats_rows}


def suggestion_probabilities(tone_counts: dict[str, int], alpha: float = 1.0) -> dict[str, float]:
    """P(tone | audience) with Laplace smoothing (Section 5.1.3); hinted as 'Warm (62%)'."""
    tones = list(tone_counts.keys()) or list(TONES)
    K = max(len(tones), len(TONES))
    total = sum(tone_counts.values())
    denom = total + alpha * K
    return {t: (tone_counts.get(t, 0) + alpha) / denom for t in tones}
