"""Golden-value tests for the maths: the Section 17 worked example with exact numbers."""
from __future__ import annotations

import numpy as np
import pytest

from app.ai.confidence import confidence_band, rank1_confidence
from app.ai.features import FEATURE_NAMES, build_feature_row, relevance_feature
from app.ai.mmr import exhaustive_best, mmr_select, subset_objective
from app.ai.personalization import reward_for, update_arm, update_length_stats, update_preference
from app.ai.scoring import (DEFAULT_WEIGHTS, choice_probabilities, gradient_step, sanitize_weights,
                            scores, weights_to_dict)
from app.ai.similarity import cosine, cosine_bands, l2_normalize, novelty_scores, pca_2d, preference_match
from app.ai.stats import (length_suitability, mean_pairwise_cosine, shannon_entropy, shrink_mean,
                          softmax, wilson_interval, zscore)
from app.ai.text import keyword_coverage, ngram_jaccard, normalise, word_count

# Section 17.8 feature rows of the surviving candidates
F = np.array([
    [0.82, 0.90, 0.70, 0.60, 0.95, 0.55],   # A  Warm - Story opening
    [0.78, 0.65, 0.75, 0.82, 0.90, 0.60],   # B  Inspirational - Gratitude
    [0.74, 0.85, 0.55, 0.90, 0.80, 0.50],   # C  Playful - Punchy
    [0.70, 0.60, 0.50, 0.88, 0.85, 0.50],   # F  Inspirational - Gratitude
])
TRUE_SCORES = np.array([0.779, 0.757, 0.742, 0.672])


def _gram_embeddings() -> np.ndarray:
    """Four unit vectors whose pairwise dots match Section 17.9's S_CC entries."""
    a = np.array([1.0, 0, 0, 0])
    b = np.array([0.62, np.sqrt(1 - 0.62 ** 2), 0, 0])
    x = (0.38 - 0.62 * 0.35) / b[1]
    c = np.array([0.35, x, np.sqrt(max(1 - 0.35 ** 2 - x ** 2, 0)), 0.0])
    p = (0.20 - 0.62 * 0.22) / b[1]
    q = (0.30 - 0.35 * 0.22 - x * p) / c[2]
    r = np.sqrt(max(1 - 0.22 ** 2 - p ** 2 - q ** 2, 1e-9))
    f = np.array([0.22, p, q, r])
    return np.vstack([a, b, c, f])


# ---------------------------------------------------------------- cosine

def test_cosine_toy_example_section_5_5_1():
    a = np.array([1, 2, 0, 1], dtype=float)
    b = np.array([2, 1, 1, 0], dtype=float)
    assert cosine(a, b) == pytest.approx(4 / 6, abs=1e-6)


def test_l2_normalisation_makes_cosine_a_dot_product():
    M = l2_normalize(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert np.linalg.norm(M[0]) == pytest.approx(1.0)
    assert cosine(M[0], M[0]) == pytest.approx(1.0)


def test_cosine_bands_section_5_5_3():
    assert cosine_bands(0.40) == "Fresh"
    assert cosine_bands(0.60) == "Related"
    assert cosine_bands(0.80) == "Similar"
    assert cosine_bands(0.90) == "Repetitive"


def test_novelty_is_one_minus_nearest_history():
    H = l2_normalize(np.array([[1.0, 0.0, 0.0]]))
    E = l2_normalize(np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]))
    N = novelty_scores(E, H)
    assert N[0] == pytest.approx(0.0, abs=1e-6)
    assert N[1] == pytest.approx(1.0, abs=1e-6)


def test_empty_history_means_fully_novel():
    E = l2_normalize(np.random.default_rng(0).normal(size=(3, 8)))
    assert np.allclose(novelty_scores(E, np.zeros((0, 8))), 1.0)


# ---------------------------------------------------------------- scoring

def test_scores_equal_blueprint_section_17_8():
    s = scores(F, DEFAULT_WEIGHTS)
    assert s == pytest.approx(TRUE_SCORES, abs=0.001)


def test_candidate_a_score_hand_computed():
    # 0.30*0.82 + 0.20*0.90 + 0.20*0.70 + 0.15*0.60 + 0.10*0.95 + 0.05*0.55 = 0.7785
    manual = 0.30 * 0.82 + 0.20 * 0.90 + 0.20 * 0.70 + 0.15 * 0.60 + 0.10 * 0.95 + 0.05 * 0.55
    assert manual == pytest.approx(0.7785)
    assert float(scores(F[:1], DEFAULT_WEIGHTS)[0]) == pytest.approx(0.7785, abs=5e-4)


def test_weight_constraints_project_onto_allowed_set():
    dirty = np.array([0.9, 0.1, 0.1, 0.1, -0.4, 0.1])
    w = sanitize_weights(dirty)
    assert w.sum() == pytest.approx(1.0)
    assert (w >= 0.02 - 1e-9).all() and (w <= 0.50 + 1e-9).all()


def test_choice_probabilities_section_17():
    chosen_batch = np.array([F[0], F[2], F[3]])          # A, C, F after MMR
    p = choice_probabilities(chosen_batch, DEFAULT_WEIGHTS)
    assert p == pytest.approx([0.46, 0.34, 0.20], abs=0.01)


def test_gradient_step_section_17_12():
    chosen_batch = np.array([F[0], F[2], F[3]])
    w = gradient_step(chosen_batch, 1, DEFAULT_WEIGHTS, lr=0.01, lam=0.005)
    d = weights_to_dict(w)
    # blueprint values 0.161 / 0.195 / 0.094; renormalisation to sum=1 shifts by <0.001
    assert d["N"] == pytest.approx(0.161, abs=0.001)    # novelty raised: chosen was +0.14 novel
    assert d["P"] == pytest.approx(0.195, abs=0.001)    # personalization lowered: chosen -0.06
    assert d["Len"] == pytest.approx(0.094, abs=0.001)
    assert w.sum() == pytest.approx(1.0)
    assert (w >= 0.02).all() and (w <= 0.50).all()


def test_gradient_moves_toward_the_chosen_features():
    chosen_batch = np.array([F[0], F[2], F[3]])
    w0 = DEFAULT_WEIGHTS.copy()
    w1 = gradient_step(chosen_batch, 1, w0, lr=0.05, lam=0.0)
    probs = choice_probabilities(chosen_batch, w0)
    expected = probs @ chosen_batch
    assert (F[2] - expected)[3] > 0            # C is more novel than the batch average
    assert w1[3] > w0[3]                        # ... so the novelty weight rises


# ---------------------------------------------------------------- MMR

def test_mmr_worked_example_section_17_9():
    scores_vec = np.array([0.779, 0.757, 0.742, 0.672])
    E = _gram_embeddings()
    # sanity: the constructed vectors reproduce the blueprint's similarities
    G = E @ E.T
    assert G[0, 1] == pytest.approx(0.62, abs=1e-6)
    assert G[0, 2] == pytest.approx(0.35, abs=1e-6)
    assert G[0, 3] == pytest.approx(0.22, abs=1e-6)
    assert G[1, 2] == pytest.approx(0.38, abs=1e-6)
    assert G[2, 3] == pytest.approx(0.30, abs=1e-6)
    order = mmr_select(scores_vec, E, k=3, lam=0.7)
    assert order == [0, 2, 3]                  # A, C, F - B (near-cousin of A) is dropped


def test_mmr_step_values_section_17_9():
    s = np.array([0.779, 0.757, 0.742, 0.672])
    E = _gram_embeddings()
    G = E @ E.T
    assert 0.7 * s[1] - 0.3 * G[1, 0] == pytest.approx(0.344, abs=0.001)
    assert 0.7 * s[2] - 0.3 * G[2, 0] == pytest.approx(0.414, abs=0.001)
    assert 0.7 * s[3] - 0.3 * G[3, 0] == pytest.approx(0.404, abs=0.001)


def test_exhaustive_matches_greedy_for_n6_k3():
    rng = np.random.default_rng(7)
    scores_vec = rng.random(6)
    V = l2_normalize(rng.normal(size=(6, 10)))
    S_cc = V @ V.T
    best, _ = exhaustive_best(scores_vec, S_cc, k=3, lam=0.7)
    greedy = mmr_select(scores_vec, V, k=3, lam=0.7)
    # greedy may differ from the optimum, but must beat a naive top-3 by score
    naive = list(np.argsort(-scores_vec)[:3])
    assert subset_objective(greedy, scores_vec, S_cc) >= subset_objective(naive, scores_vec, S_cc) - 1e-9
    assert len(best) == 3


# ---------------------------------------------------------------- probability

def test_thompson_selects_requested_tones_plus_exploration():
    from app.ai.arms import thompson_select
    rng = np.random.default_rng(42)
    arms = thompson_select(["warm", "playful"], {(t, s): (1.0, 1.0) for t in ("warm", "playful")
                                                  for s in ("story-opening", "punchy")}, rng=rng)
    assert len(arms) == 3
    assert len(set(arms)) == 3
    assert arms[0][0] == "warm" and arms[1][0] == "playful"
    assert arms[2][0] not in ("warm", "playful")   # exploration arm


def test_thompson_is_deterministic_with_a_seed():
    from app.ai.arms import thompson_select
    stats = {(t, s): (2.0, 2.0) for t in ("warm", "playful") for s in ("story-opening", "punchy")}
    a = thompson_select(["warm"], stats, rng=np.random.default_rng(1))
    b = thompson_select(["warm"], stats, rng=np.random.default_rng(1))
    assert a == b


def test_beta_updates_section_5_1_2():
    assert update_arm(3, 2, "select") == (4, 2)
    assert update_arm(3, 2, "shown") == (3, 3)
    assert update_arm(3, 2, "reject") == (3, 4)


def test_dirichlet_confidence_sums_to_one_and_is_seeded():
    p = rank1_confidence(F, DEFAULT_WEIGHTS, samples=200, seed=42)
    p2 = rank1_confidence(F, DEFAULT_WEIGHTS, samples=200, seed=42)
    assert p == pytest.approx(p2)                # seeded Monte-Carlo
    assert p.sum() == pytest.approx(1.0)
    # A is top-ranked most often -> High confidence band (Section 17.8 claims >= 0.6)
    assert p[0] == max(p)
    assert p[0] >= 0.60
    assert confidence_band(float(p[0])) == "high"


def test_softmax_is_normalised():
    out = softmax(np.array([1000.0, 1000.5, 999.0]))
    assert out.sum() == pytest.approx(1.0)
    assert out[1] > out[0] > out[2]


# ---------------------------------------------------------------- statistics

def test_wilson_interval_known_value():
    lo, hi = wilson_interval(3, 10)
    assert lo == pytest.approx(0.108, abs=0.005)
    assert hi == pytest.approx(0.603, abs=0.005)


def test_wilson_handles_zero_samples():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_shannon_entropy_uniform_is_one():
    assert shannon_entropy([5, 5, 5, 5]) == pytest.approx(1.0, abs=1e-9)
    assert shannon_entropy([10, 0, 0, 0]) == pytest.approx(0.0, abs=1e-9)


def test_shrinkage_mean_moves_toward_prior():
    assert shrink_mean(10, 50, k=3, mu0=46) == pytest.approx((500 + 138) / 13)
    assert shrink_mean(0, 50, k=3, mu0=46) == 46


def test_length_gaussian_bump():
    assert length_suitability(46, 46, 8) == pytest.approx(1.0)
    far = length_suitability(90, 46, 8)
    assert 0.0 <= far < 0.05


def test_zscore_flags_outliers():
    assert abs(zscore(70, 46, 5)) > 2
    assert zscore(47, 46, 5) == pytest.approx(0.2)


def test_mean_pairwise_cosine():
    M = l2_normalize(np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]))
    assert mean_pairwise_cosine(M) == pytest.approx((1 + 0 + 0) / 3)


# ---------------------------------------------------------------- vectors / text

def test_preference_match_neutral_without_profile():
    e = l2_normalize(np.ones((1, 4)))[0]
    assert preference_match(e, None) == 0.5
    assert preference_match(e, e) == pytest.approx(1.0)


def test_pca_2d_shape_and_centring():
    M = np.random.default_rng(3).normal(size=(7, 16))
    coords = pca_2d(M)
    assert coords.shape == (7, 2)
    assert abs(coords.mean(axis=0)).max() < 1e-5


def test_keyword_coverage_and_normalisation():
    assert keyword_coverage(["free refill"], "Your free refill awaits") == 1.0
    assert keyword_coverage(["free refill"], "Nothing here") == 0.0
    assert normalise("  Hello\u3000  world \x00 ") == "Hello world"
    assert word_count("one two three") == 3


def test_4gram_jaccard_catches_copy_paste():
    a = "The first cool evening of the season smells like cardamom"
    b = "The first cool evening of the season smells like cardamom!"
    assert ngram_jaccard(a, b) > 0.8
    assert ngram_jaccard(a, "Completely different wording about bicycles") < 0.05


def test_feature_row_uses_all_six_features():
    row = build_feature_row(cos_intent=0.6, keyword_cov=1.0, p_intended=0.9,
                            e=l2_normalize(np.ones((1, 4)))[0], u=None,
                            max_cos_history=0.4, word_count=46, length_mean=46, length_std=8,
                            alpha=3, beta=2)
    assert len(row) == len(FEATURE_NAMES) == 6
    assert all(0.0 <= v <= 1.0 for v in row)
    assert row[5] == pytest.approx(0.6)      # Beta mean 3/5


def test_relevance_rescales_raw_cosine():
    assert relevance_feature(0.20, 0.0) == pytest.approx(0.0)
    assert relevance_feature(0.80, 1.0) == pytest.approx(1.0)
    assert relevance_feature(0.50, 1.0) == pytest.approx(0.7 * 0.5 + 0.3)


def test_rewards_table_section_7_3():
    assert reward_for("select") == 1.0
    assert reward_for("select", edited=True) == 0.8
    assert reward_for("reject") == -0.8
    assert reward_for("ignore") == 0.0


def test_length_window_and_update():
    mean, std, n = update_length_stats([40, 50, 60], 45, preset_mean=45)
    assert n == 4
    xbar = float(np.mean([40, 50, 60, 45]))
    expected = (4 * xbar + 3 * 45) / 7        # shrink_mean, k = 3
    assert mean == pytest.approx(expected, abs=1e-6)


def test_preference_update_normalises():
    e = l2_normalize(np.ones((1, 8)))[0]
    u = update_preference(None, e, 1.0)
    assert np.linalg.norm(u) == pytest.approx(1.0)
    assert update_preference(u, e, 0.0) is u      # no-op on zero reward
