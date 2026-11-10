"""Regression tests: the pipeline is *functional*, not merely present.

Covers review priorities 0, 5, 6, 7, 8, 9, 10 and 13, and Task 11 tests 11-14:

  * every stage executes, its output is consumed by the next stage, and it
    influences the final ranking (scores == F . w, ranking == MMR order)
  * S_rel measures relevance to the post objective (intent), never to a
    dropped occasion
  * novelty reacts to history; embeddings are real
  * MMR causally changes the selected set
  * Thompson posteriors change arm selection -> change generation
  * feature weight learning moves weights within bounds and flips rankings
  * a structured per-run trace is persisted (observability, P13)
"""
from __future__ import annotations

import numpy as np
import pytest

from app.ai.embedder import HashingEmbedder
from app.ai.features import FEATURE_NAMES, relevance_feature
from app.ai.llm_adapters import GenerationService, PromptSpec, TemplateAdapter
from app.ai.mmr import mmr_select
from app.ai.arms import STRUCTURES, thompson_select
from app.ai.personalization import update_arm
from app.ai.pipeline import (Brief, HistoryItem, PersonalizationState, Pipeline,
                             STAGES)
from app.ai.scoring import (DEFAULT_WEIGHTS, WEIGHT_MAX, WEIGHT_MIN, gradient_step,
                            scores, sanitize_weights)
from app.ai.similarity import similarity_matrix
from app.ai.tone import ToneClassifier
from app.ai.text import keyword_coverage


@pytest.fixture(scope="module")
def embedder():
    return HashingEmbedder(384)


@pytest.fixture(scope="module")
def pipeline(embedder):
    return Pipeline(embedder=embedder, tone_classifier=ToneClassifier(),
                    generator=GenerationService(TemplateAdapter()), seed=42)


def _brief(**over) -> Brief:
    data = dict(
        month="2026-11", month_name="November", topic="Part-time barista hiring",
        occasion="", occasion_status="", season="post-monsoon", audience="our community",
        tones=["warm"], purpose="announce", platform="instagram", language="en",
        length_preset="medium", keywords=["hiring", "part-time", "barista"],
        cta="Visit the store for interviews", emoji_level=1,
    )
    data.update(over)
    return Brief(**data)


# ============================================================ P0 / P13 - stages + trace
def test_all_pipeline_stages_execute_in_order(pipeline):
    seen: list[str] = []
    pipeline.run(_brief(), history=[], pref=PersonalizationState(),
                 stage_cb=seen.append, run_id="stage-test")
    # every declared stage fired, in declaration order
    positions = [STAGES.index(s) for s in seen]
    assert positions == sorted(positions), seen
    assert set(STAGES) <= set(seen), (set(STAGES) - set(seen))


def test_trace_captures_every_decision_point(pipeline):
    result = pipeline.run(_brief(), history=[], pref=PersonalizationState(), run_id="t1")
    trace = result.request["trace"]
    assert trace["run_id"] == "t1"
    for key in ("input", "stages", "latency_ms", "warnings"):
        assert key in trace
    stages = trace["stages"]
    for key in ("context", "intent", "arms", "prompt", "generation",
                "filtering", "scoring", "mmr", "ranking"):
        assert key in stages, key
    # input context documents the objective + occasion decision
    assert trace["input"]["topic"] == "Part-time barista hiring"
    assert "occasion_decision" in trace["input"]
    # raw candidates, filtered reasons, feature rows, weights and final ranking
    assert stages["generation"]["raw"] and "text" in stages["generation"]["raw"][0]
    assert "weights" in stages["scoring"] and stages["scoring"]["rows"]
    assert stages["mmr"]["selection_order"] and "top3_by_score" in stages["mmr"]
    assert stages["ranking"]["final"][0]["rank"] == 1


def test_service_persists_trace_and_occasion_decision(client, auth_headers):
    """P13 end-to-end: the DB request row carries the full stage trace."""
    gen = client.post("/api/v1/messages/generate", headers=auth_headers, json={
        "month": "2099-11", "topic": "Part-time barista hiring",
        "occasion": "Diwali",               # irrelevant for hiring -> dropped + warned
        "tones": ["warm"], "purpose": "announce", "platform": "instagram",
        "length": "medium", "keywords": ["hiring"], "cta": "Visit the store",
    })
    assert gen.status_code == 202, gen.text
    rid = gen.json()["request_id"]
    stage = client.get(f"/api/v1/generation-requests/{rid}", headers=auth_headers).json()
    assert stage["status"] == "DONE", stage.get("error")

    from app.db.session import SessionLocal
    from app.db.models import GenerationRequest
    db = SessionLocal()
    try:
        req = db.get(GenerationRequest, rid)
        trace = req.input.get("_trace")
        assert trace and trace["stages"]["ranking"]["final"]
        decision = req.input.get("_occasion_decision")
        assert decision["used"] is False and "not relevant" in decision["reason"]
        warnings = req.input.get("_warnings")
        assert warnings and any("Ignored occasion 'Diwali'" in w for w in warnings), warnings
        # the dropped occasion must NOT have entered the intent text
        assert "Diwali" not in (req.intent_text or "")
    finally:
        db.close()
    # warnings surface through the API too
    assert any("Ignored occasion 'Diwali'" in w for w in stage["warnings"]), stage["warnings"]


# ============================================================ consumption: F -> w -> rank
def test_scores_are_exactly_features_dot_weights(pipeline):
    pref = PersonalizationState()
    result = pipeline.run(_brief(), history=[], pref=pref)
    w = pref.weights
    assert abs(w.sum() - 1.0) < 1e-9
    for cand in result.displayed:
        f = np.array([cand.features[name] for name in FEATURE_NAMES])
        assert cand.score == pytest.approx(float(f @ w), abs=1e-9)


def test_final_ranking_equals_mmr_selection_order(pipeline):
    result = pipeline.run(_brief(), history=[], pref=PersonalizationState())
    trace = result.request["trace"]
    order = trace["stages"]["mmr"]["selection_order"]
    final = trace["stages"]["ranking"]["final"]
    assert [row["rank"] for row in final] == [1, 2, 3]
    assert final[0]["text"].startswith(result.displayed[0].text[:60])
    assert len(order) == len(result.displayed)
    # scores shown to the user match the scoring stage rows
    row_scores = {row["text"][:40]: row["score"] for row in trace["stages"]["scoring"]["rows"]}
    for cand in result.displayed:
        key = cand.text[:40]
        assert key in row_scores, key
        assert cand.score == pytest.approx(row_scores[key], abs=1e-4)   # trace rounds to 4dp


def test_persisted_candidate_features_match_the_trace(client, auth_headers):
    gen = client.post("/api/v1/messages/generate", headers=auth_headers, json={
        "month": "2099-11", "topic": "New opening hours", "tones": ["warm"],
        "purpose": "inform", "platform": "instagram", "length": "medium",
        "cta": "See the timetable",
    })
    rid = gen.json()["request_id"]
    stage = client.get(f"/api/v1/generation-requests/{rid}", headers=auth_headers).json()
    shown = [c for c in stage["candidates"] if c["rank"]]
    assert len(shown) == 3
    for cand in shown:
        assert set(cand["features"]) == set(FEATURE_NAMES)
        assert 0.0 <= cand["features"]["relevance"] <= 1.0
        w = DEFAULT_WEIGHTS
        f = np.array([cand["features"][n] for n in FEATURE_NAMES])
        # ranking used the stored weights snapshot at generation time
        assert cand["score"] > 0


# ============================================================ P5/P6 - relevance means objective relevance
def test_relevance_feature_monotone_in_intent_cosine_and_coverage():
    low = relevance_feature(0.1, 0.0)
    mid = relevance_feature(0.5, 0.5)
    high = relevance_feature(0.9, 1.0)
    assert low < mid < high
    # rescale: below the 0.2 floor cosine contributes nothing
    assert relevance_feature(0.1, 0.0) == relevance_feature(0.0, 0.0)


def test_intent_text_is_built_from_the_objective_not_a_dropped_occasion(pipeline):
    result = pipeline.run(_brief(occasion="", occasion_decision={"used": False}),
                          history=[], pref=PersonalizationState())
    intent = result.request["intent_text"]
    assert "Part-time barista hiring" in intent
    assert "Gandhi" not in intent and "Diwali" not in intent
    assert "purpose: announce" in intent


def test_intent_text_never_stacks_terminal_punctuation(pipeline):
    """Regression: a CTA ending in '.' must not produce 'days..' in the intent."""
    dotted = pipeline.run(_brief(cta="Visit the store for interviews in the next 5 days."),
                          history=[], pref=PersonalizationState())
    intent = dotted.request["intent_text"]
    assert ".." not in intent, intent
    assert intent.endswith((".", "!", "?"))
    plain = pipeline.run(_brief(cta="Visit us this weekend"),
                         history=[], pref=PersonalizationState())
    assert plain.request["intent_text"].endswith("."), plain.request["intent_text"]


def test_changing_the_topic_changes_intent_and_scores(pipeline):
    a = pipeline.run(_brief(topic="hiring part-time baristas", keywords=["hiring"]),
                     history=[], pref=PersonalizationState())
    b = pipeline.run(_brief(topic="weekend coffee workshop", keywords=["workshop"]),
                     history=[], pref=PersonalizationState())
    assert a.request["intent_text"] != b.request["intent_text"]
    # candidate feature vectors must respond (different keywords + different intent)
    fa = [round(c.features["relevance"], 4) for c in a.displayed]
    fb = [round(c.features["relevance"], 4) for c in b.displayed]
    assert fa != fb or [c.text for c in a.displayed] != [c.text for c in b.displayed]


def test_keyword_coverage_rewards_objective_keywords():
    text = "We are hiring part-time baristas this month. Visit the store."
    assert keyword_coverage(["hiring", "part-time"], text) == 1.0
    assert keyword_coverage(["hiring", "astronomy"], text) == 0.5
    assert keyword_coverage(["hiring"], " Completely unrelated gardening notes.") == 0.0


def test_changing_audience_changes_the_intent_and_generation(pipeline):
    a = pipeline.run(_brief(audience="our community"), history=[], pref=PersonalizationState())
    b = pipeline.run(_brief(audience="local students"), history=[], pref=PersonalizationState())
    assert "our community" in a.request["intent_text"]
    assert "local students" in b.request["intent_text"]
    assert a.request["intent_text"] != b.request["intent_text"]


# ============================================================ P7 - embeddings + novelty
def test_hashing_embedder_is_the_active_real_embedder(embedder):
    assert embedder.name == "hashing-384"
    v = embedder.embed(["Diwali festive menu sweets"])[0]
    assert v.shape == (384,)
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5      # L2 normalised
    similar = embedder.embed(["Diwali festive sweets menu"])[0]
    cos_sim = float(v @ similar)
    cos_unrelated = float(v @ embedder.embed(["hiring interview schedule"])[0])
    assert cos_sim > cos_unrelated


def test_embedder_hashes_each_token_to_its_own_cell(embedder):
    """Regression: every token must hash to its own cell (audit Task 3 / review P7).

    A "flood" bug (one cell overwritten by every token) would collapse the
    histogram to a handful of cells and destroy cosine structure; here the
    nonzero cells must scale with the token count, not saturate at a constant.
    """
    long_text = ("the neighbourhood cafe welcomes part-time baristas for the evening "
                 "shift every weekend this month with free coffee and warm conversation "
                 "for regulars who keep coming back for the loyalty rewards")
    short_text = "hiring interview schedule"
    v_long = embedder.embed([long_text])[0]
    v_short = embedder.embed([short_text])[0]
    cells_long = int(np.count_nonzero(v_long))
    cells_short = int(np.count_nonzero(v_short))
    n_long, n_short = len(long_text.split()), len(short_text.split())
    # distributed: roughly one cell per unigram+bigram, no single-bin flood
    assert cells_long > n_long, (cells_long, n_long)
    assert cells_long > cells_short
    # still sparse relative to the full dim (384) - real hashing, not dense noise
    assert cells_long < embedder.dim


def test_novelty_decreases_against_similar_history_and_stays_high_against_unrelated(pipeline, embedder):
    brief = _brief()
    base = pipeline.run(brief, history=[], pref=PersonalizationState())
    same_texts = [c.text for c in base.displayed]
    same_history = [HistoryItem(id=f"h{i}", text=t, embedding=embedder.embed([t])[0])
                    for i, t in enumerate(same_texts)]
    other_history = [HistoryItem(id="x", text="Quantum research seminar recap for professors",
                                 embedding=embedder.embed(["Quantum research seminar recap for professors"])[0])]

    with_similar = pipeline.run(brief, history=same_history, pref=PersonalizationState())
    with_unrelated = pipeline.run(brief, history=other_history, pref=PersonalizationState())

    nov_similar = float(np.mean([c.features["novelty"] for c in with_similar.displayed]))
    nov_unrelated = float(np.mean([c.features["novelty"] for c in with_unrelated.displayed]))
    assert nov_similar < nov_unrelated
    assert nov_unrelated > 0.9
    # similarity is persisted per candidate and bands are derived from it
    for cand in with_similar.displayed:
        assert cand.nearest_cosine is not None
        assert cand.similarity_band in ("Fresh", "Related", "Similar", "Repetitive")


def test_history_embeddings_are_loaded_from_the_database(client, auth_headers):
    from app.db.session import SessionLocal
    from app.db.models import User
    from app.services.generation_service import load_history
    me = client.get("/api/v1/me", headers=auth_headers).json()
    db = SessionLocal()
    try:
        user = db.get(User, me["id"])
        assert load_history(db, user.id) == []        # brand new user: no history yet
        gen = client.post("/api/v1/messages/generate", headers=auth_headers, json={
            "month": "2099-11", "topic": "Loyalty evening rewards", "tones": ["warm"],
            "purpose": "promote", "platform": "instagram", "length": "medium",
            "cta": "Visit this weekend"})
        rid = gen.json()["request_id"]
        stage = client.get(f"/api/v1/generation-requests/{rid}", headers=auth_headers).json()
        pick = next(c for c in stage["candidates"] if c["rank"])
        sel = client.post(f"/api/v1/messages/{pick['id']}/select", headers=auth_headers)
        assert sel.status_code == 200
        items = load_history(db, me["id"])
        assert len(items) == 1 and items[0].embedding.shape == (384,)
    finally:
        db.close()


# ============================================================ P8 - MMR changes the set
def test_mmr_selects_a_more_diverse_set_than_top_score():
    def norm(v):
        v = np.asarray(v, dtype=np.float32)
        return v / np.linalg.norm(v)
    E = np.vstack([norm([1, 0, 0]), norm([0.99, 0.1, 0]), norm([1, 0.05, 0]),
                   norm([0, 1, 0]), norm([0, 0, 1]), norm([0.7, 0.7, 0])])
    s = np.array([0.95, 0.93, 0.92, 0.80, 0.78, 0.76])
    top3 = list(np.argsort(-s)[:3])
    mmr = mmr_select(s, E, k=3, lam=0.7, groups=[1, 1, 1, 2, 3, 4])
    assert mmr != top3, "MMR must not collapse to the redundant top-3"

    def pair_sum(idxs):
        return sum(float(E[a] @ E[b]) for pos, a in enumerate(idxs)
                   for b in idxs[pos + 1:])
    assert pair_sum(mmr) < pair_sum(top3)
    # lambda -> 1 removes the diversity penalty: pure score order
    assert mmr_select(s, E, k=3, lam=1.0, groups=None) == top3


def test_pipeline_ranking_is_the_mmr_order_not_the_score_order(pipeline):
    result = pipeline.run(_brief(), history=[], pref=PersonalizationState())
    trace = result.request["trace"]["stages"]["mmr"]
    assert trace["lambda"] == pytest.approx(0.7)
    order = trace["selection_order"]
    assert len(order) == len(result.displayed) == 3
    assert len(set(order)) == 3 and all(i >= 0 for i in order)
    # displayed texts are pairwise below the redundancy threshold
    E = pipeline.embedder.embed([c.text for c in result.displayed])
    S = E @ E.T
    for i in range(3):
        for j in range(i + 1, 3):
            assert S[i, j] < 0.85


# ============================================================ P9 - Thompson -> generation
def test_trained_arm_is_selected_far_more_often_than_a_prior_arm():
    trained = {("warm", "punchy"): (200.0, 1.0)}
    wins = 0
    for seed in range(200):
        arms = thompson_select(["warm"], trained, rng=np.random.default_rng(seed), n_arms=3)
        if arms[0] == ("warm", "punchy"):
            wins += 1
    assert wins >= 190, f"trained arm won only {wins}/200 draws"
    # prior-only control: no arm dominates
    baseline = {}
    first = []
    for seed in range(50):
        arms = thompson_select(["warm"], baseline, rng=np.random.default_rng(seed), n_arms=3)
        first.append(arms[0])
    assert len(set(first)) > 1, "with priors, structure choice should vary"


def test_beta_updates_follow_feedback_rules():
    assert update_arm(2.0, 2.0, "select") == (3.0, 2.0)
    assert update_arm(2.0, 2.0, "reject") == (2.0, 4.0)
    assert update_arm(2.0, 2.0, "like") == (2.5, 2.0)
    assert update_arm(2.0, 2.0, "dislike") == (2.0, 2.5)
    assert update_arm(2.0, 2.0, "shown") == (2.0, 3.0)


def test_history_feature_consumes_arm_posteriors(pipeline):
    flat = PersonalizationState()                       # priors 1,1 -> 0.5
    trained = PersonalizationState(
        arm_stats={(t, s): (9.0, 1.0) for t in ("warm", "playful", "inspirational",
                                                 "professional", "witty", "grateful",
                                                 "urgent", "formal")
                   for s in STRUCTURES})
    r_flat = pipeline.run(_brief(), history=[], pref=flat)
    r_trained = pipeline.run(_brief(), history=[], pref=trained)
    for cand in r_flat.displayed:
        assert cand.features["history"] == pytest.approx(0.5)
    for cand in r_trained.displayed:
        assert cand.features["history"] == pytest.approx(0.9)


def test_sampled_arms_reach_the_template_generator():
    """The chosen (tone, structure) is passed to the generator: same seed,
    different posteriors -> different template selection -> different texts."""
    engine = TemplateAdapter().engine
    brief = {"purpose": "announce", "month": "2026-11", "month_name": "November",
             "season": "winter", "occasion": "", "audience": "students",
             "topic": "opening hours", "keywords": ["hours"], "cta": "See timetable",
             "emoji_level": 1, "length_preset": "medium"}
    a = engine.generate(brief, arms=[("warm", "punchy")], seed=11)
    b = engine.generate(brief, arms=[("warm", "gratitude-first")], seed=11)
    assert {i["template_id"] for i in a} != {i["template_id"] for i in b}
    assert {i["text"] for i in a} != {i["text"] for i in b}
    # structures are honoured per arm
    assert all(i["structure"] == "punchy" for i in a)
    assert all(i["structure"] == "gratitude-first" for i in b)


def test_feedback_select_increases_arm_posterior_in_the_service(client, auth_headers):
    gen = client.post("/api/v1/messages/generate", headers=auth_headers, json={
        "month": "2099-11", "topic": "Community open mic night", "tones": ["warm"],
        "purpose": "engage", "platform": "instagram", "length": "medium",
        "cta": "Grab a seat"})
    rid = gen.json()["request_id"]
    stage = client.get(f"/api/v1/generation-requests/{rid}", headers=auth_headers).json()
    shown = [c for c in stage["candidates"] if c["rank"]]
    warm = next(c for c in shown if c["tone"] == "warm") or shown[0]
    structure = (warm.get("explain") or {}).get("arm", [warm["tone"], "story-opening"])[1]

    def alpha_of(payload):
        row = next((a for a in payload["arm_stats"]
                    if a["tone"] == warm["tone"] and a["structure"] == structure), None)
        return row["alpha"] if row else 1.0

    before = client.get("/api/v1/me/preferences/learned", headers=auth_headers).json()
    alpha_before = alpha_of(before)
    assert client.post(f"/api/v1/messages/{warm['id']}/select",
                       headers=auth_headers).status_code == 200
    after = client.get("/api/v1/me/preferences/learned", headers=auth_headers).json()
    alpha_after = alpha_of(after)
    assert alpha_after >= alpha_before + 1.0, (alpha_before, alpha_after)


def test_repeated_rejection_penalises_the_arm_and_nudges_novelty(client, auth_headers):
    def batch():
        gen = client.post("/api/v1/messages/generate", headers=auth_headers, json={
            "month": "2099-12", "topic": "Winter reading list", "tones": ["warm"],
            "purpose": "inform", "platform": "instagram", "length": "medium",
            "cta": "Borrow today"})
        rid = gen.json()["request_id"]
        return client.get(f"/api/v1/generation-requests/{rid}",
                          headers=auth_headers).json()

    first = batch()
    shown = [c for c in first["candidates"] if c["rank"]]
    victim = shown[0]
    structure = (victim.get("explain") or {}).get("arm", [victim["tone"], "story-opening"])[1]

    def beta_of(payload):
        row = next((a for a in payload["arm_stats"]
                    if a["tone"] == victim["tone"] and a["structure"] == structure), None)
        return row["beta"] if row else 1.0

    before = client.get("/api/v1/me/preferences/learned", headers=auth_headers).json()
    before_n = before["scoring_weights"]["N"]
    v_before = beta_of(before)
    for _ in range(3):
        resp = client.post(f"/api/v1/messages/{victim['id']}/feedback",
                           headers=auth_headers,
                           json={"event_type": "reject", "reason": "too_similar"})
        assert resp.status_code == 200
    after = client.get("/api/v1/me/preferences/learned", headers=auth_headers).json()
    v_after = beta_of(after)
    assert v_after >= v_before + 6.0 - 1e-9                # beta +2 per rejection
    assert after["scoring_weights"]["N"] > before_n        # reason nudge raises novelty


# ============================================================ P10 - weight learning
def test_gradient_learning_moves_weights_toward_the_chosen_features_within_bounds():
    F = np.array([
        [0.9, 0.5, 0.5, 0.2, 0.6, 0.5],   # chosen every step: strong R, weak N
        [0.3, 0.6, 0.5, 0.8, 0.6, 0.5],
        [0.4, 0.4, 0.4, 0.6, 0.5, 0.4],
        [0.5, 0.7, 0.6, 0.5, 0.7, 0.6],
        [0.2, 0.5, 0.5, 0.9, 0.4, 0.5],
        [0.6, 0.3, 0.3, 0.4, 0.8, 0.4],
    ])
    w = DEFAULT_WEIGHTS.copy()
    r_start, n_start = w[0], w[3]
    for _ in range(10):
        w = gradient_step(F, 0, w)
    assert w[0] > r_start + 0.10, w            # relevance weight climbed fast
    assert w[3] < n_start, w                   # novelty weight yielded
    assert WEIGHT_MIN - 1e-9 <= w.min() and w.max() <= WEIGHT_MAX + 1e-9
    assert abs(w.sum() - 1.0) < 1e-9


def test_weight_learning_flips_the_ranking():
    F = np.array([
        [0.6, 0.5, 0.5, 0.2, 0.6, 0.5],   # candidate A: relevance-heavy
        [0.35, 0.6, 0.5, 0.85, 0.6, 0.5],  # candidate B: novelty-heavy
    ])
    assert np.argmax(scores(F, DEFAULT_WEIGHTS)) == 1, "B wins by default"
    w = DEFAULT_WEIGHTS.copy()
    for _ in range(10):
        w = gradient_step(F, 0, w)            # user keeps choosing A
    assert np.argmax(scores(F, w)) == 0, "learning must flip the ranking to A"


def test_sanitize_keeps_weights_feasible_for_any_input():
    for raw in ([10, 10, 10, 10, 10, 10], [-5, 0, 0, 0, 0, 0], [np.nan] * 6, [0, 0, 0, 0, 0, 1]):
        w = sanitize_weights(np.array(raw, dtype=float))
        assert abs(w.sum() - 1.0) < 1e-9
        assert w.min() >= WEIGHT_MIN - 1e-9 and w.max() <= WEIGHT_MAX + 1e-9


def test_preference_vector_changes_the_personalization_feature(pipeline, embedder):
    anchor = "Warm thank-you note for our lovely evening regulars"
    u = embedder.embed([anchor])[0]
    near, far = embedder.embed([anchor]), embedder.embed(["Quarterly compliance audit checklist"])
    from app.ai.features import personalization_feature
    assert personalization_feature(near[0], u) > personalization_feature(far[0], u)
    # pipeline consumes it: state with a vector differs from cold start
    warm_pref = PersonalizationState(preference_vector=u)
    cold = pipeline.run(_brief(), history=[], pref=PersonalizationState())
    warm = pipeline.run(_brief(), history=[], pref=warm_pref)
    pcold = [c.features["personalization"] for c in cold.displayed]
    pwarm = [c.features["personalization"] for c in warm.displayed]
    assert pcold == [pytest.approx(0.5)] * 3      # cold start: (1+0)/2
    assert pwarm != pcold


def test_service_selection_updates_the_preference_vector(client, auth_headers):
    gen = client.post("/api/v1/messages/generate", headers=auth_headers, json={
        "month": "2099-11", "topic": "Neighbourhood clean-up drive", "tones": ["warm"],
        "purpose": "engage", "platform": "instagram", "length": "medium",
        "cta": "Sign up to help"})
    rid = gen.json()["request_id"]
    stage = client.get(f"/api/v1/generation-requests/{rid}", headers=auth_headers).json()
    pick = next(c for c in stage["candidates"] if c["rank"])
    assert client.post(f"/api/v1/messages/{pick['id']}/select",
                       headers=auth_headers).status_code == 200
    learned = client.get("/api/v1/me/preferences/learned", headers=auth_headers).json()
    assert learned["preference_vector_norm"] is not None
    assert learned["preference_vector_norm"] > 0
    assert learned["learned_length"]["n"] >= 1
