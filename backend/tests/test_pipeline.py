"""Pipeline tests: end-to-end run with the template provider, constraints, diversity."""
from __future__ import annotations

import numpy as np
import pytest

from app.ai.llm_adapters import GenerationService, PromptSpec, TemplateAdapter
from app.ai.pipeline import Brief, Candidate, HistoryItem, PersonalizationState, Pipeline
from app.ai.embedder import HashingEmbedder
from app.ai.tone import ToneClassifier, confusion_matrix, load_or_train
from app.ai.text import normalise
from app.core.config import settings


@pytest.fixture(scope="module")
def embedder():
    return HashingEmbedder(384)


@pytest.fixture(scope="module")
def tone_clf(embedder):
    import os
    csv_path = os.path.join(settings.data_dir, "tone_training.csv")
    if not os.path.exists(csv_path):
        import subprocess, sys
        subprocess.run([sys.executable,
                        os.path.join(os.path.dirname(settings.data_dir), "scripts",
                                     "generate_tone_data.py")], check=False)
    return load_or_train(csv_path, embedder)


@pytest.fixture(scope="module")
def pipeline(embedder, tone_clf):
    return Pipeline(embedder=embedder, tone_classifier=tone_clf,
                    generator=GenerationService(TemplateAdapter()), seed=42)


def make_brief(**overrides) -> Brief:
    data = dict(
        month="2026-11", month_name="November", topic="Evening coffee loyalty rewards",
        occasion="Start of festive season", occasion_fact="celebrations, gifting, gatherings",
        season="post-monsoon", audience="Young professionals",
        audience_description="25-35, city workers", tones=["warm", "playful"],
        purpose="promote", platform="instagram", language="en", length_preset="medium",
        keywords=["free refill", "evening"], cta="Visit us this weekend",
        voice_notes="friendly cafe voice", banned_words=["cheap"], emoji_level=1,
    )
    data.update(overrides)
    return Brief(**data)


# ---------------------------------------------------------------- full run

def test_pipeline_produces_three_ranked_candidates(pipeline):
    result = pipeline.run(make_brief(), history=[], pref=PersonalizationState())
    assert len(result.displayed) == 3
    assert [c.rank for c in result.displayed] == [1, 2, 3]
    assert result.request["provider"] == "template"
    assert result.request["intent_text"].startswith("Monthly instagram post")
    assert len(result.arms) == 3
    for cand in result.displayed:
        assert cand.score > 0
        assert set(cand.features) == {"relevance", "tone", "personalization", "novelty", "length", "history"}
        assert all(0.0 <= v <= 1.0 for v in cand.features.values())
        assert cand.p_rank1 is not None and 0.0 <= cand.p_rank1 <= 1.0
        assert cand.confidence in ("high", "medium", "low")
        assert cand.explain.get("reasons"), "every card needs plain-language reasons"


def test_hard_constraints_hold_for_displayed_candidates(pipeline):
    result = pipeline.run(make_brief(), history=[], pref=PersonalizationState())
    lo, hi = 34, 69                      # medium preset +/- 15% (Section 4 step 9)
    for cand in result.displayed:
        assert lo <= cand.word_count <= hi, cand.text
        assert len(cand.text) <= 2200     # instagram platform limit
        assert "visit" in cand.text.lower() and "weekend" in cand.text.lower()  # CTA present
        assert "cheap" not in cand.text.lower()


def test_displayed_candidates_are_diverse(pipeline, embedder):
    result = pipeline.run(make_brief(), history=[], pref=PersonalizationState())
    texts = [c.text for c in result.displayed]
    assert len(set(map(normalise, texts))) == 3          # no duplicates
    E = embedder.embed(texts)
    S = E @ E.T
    for i in range(3):
        for j in range(i + 1, 3):
            assert S[i, j] < 0.85, "US-11: pairwise cosine among shown candidates < 0.85"


def test_context_switch_changes_output(pipeline):
    a = pipeline.run(make_brief(topic="winter opening hours"), history=[], pref=PersonalizationState())
    b = pipeline.run(make_brief(topic="loyalty card launch", tones=["professional"], purpose="announce"),
                     history=[], pref=PersonalizationState())
    assert a.request["intent_text"] != b.request["intent_text"]
    assert {c.tone for c in a.displayed} != {c.tone for c in b.displayed} or \
           a.displayed[0].text != b.displayed[0].text


# ---------------------------------------------------------------- history / novelty

def test_novelty_against_history(pipeline, embedder):
    result = pipeline.run(make_brief(), history=[], pref=PersonalizationState())
    past_text = result.displayed[0].text
    history = [HistoryItem(id="past1", text=past_text,
                           embedding=embedder.embed([past_text])[0])]
    result2 = pipeline.run(make_brief(), history=history, pref=PersonalizationState())
    # every candidate must now be measured against the stored post
    for cand in result2.displayed:
        assert cand.nearest_cosine is not None
        if cand.nearest_cosine >= 0.50:
            assert cand.similarity_band in ("Related", "Similar", "Repetitive")


def test_exact_duplicate_of_history_is_filtered(pipeline):
    from app.ai.pipeline import Pipeline
    brief = make_brief()
    old_post = ("A totally different old post about coffee and cardamom mornings with free refill "
                "evening visits for young professionals this weekend at our cafe, where the team "
                "keeps the lights low and the playlist soft and everyone is welcome to stay a "
                "little longer than they planned to ")
    history_norm = {normalise(old_post)}
    cand = Candidate(text=old_post, word_count=len(old_post.split()), char_count=len(old_post))
    reason = Pipeline._hard_constraint_reason(cand, brief, history_norm, keywords=brief.keywords)
    assert reason == "duplicate_of_history"


def test_hard_constraint_reasons(pipeline):
    from app.ai.pipeline import Pipeline
    brief = make_brief()
    ok = {"free refill evening visit weekend cafe cosy cardamom coffee loyalty reward special offer"}
    words_ok = " ".join(["word"] * 50)

    short = Candidate(text="Too short.", word_count=2, char_count=10)
    assert Pipeline._hard_constraint_reason(short, brief, set(), keywords=[]) == "too_short"

    compliant = Candidate(text=(words_ok + " visit us this weekend"), word_count=54,
                          char_count=len(words_ok) + 24)
    assert Pipeline._hard_constraint_reason(compliant, brief, set(), keywords=[]) is None

    banned = Candidate(text=("cheap " + words_ok), word_count=51, char_count=60)
    assert Pipeline._hard_constraint_reason(banned, brief, set(), keywords=[]) == "banned_word"

    no_cta = Candidate(text=words_ok, word_count=50, char_count=55)
    assert Pipeline._hard_constraint_reason(no_cta, brief, set(), keywords=[]) == "missing_cta"


def test_empty_history_and_cold_start(pipeline):
    state = PersonalizationState()
    result = pipeline.run(make_brief(), history=[], pref=state)
    for cand in result.displayed:
        assert cand.features["novelty"] == pytest.approx(1.0)   # nothing to repeat
        assert cand.features["personalization"] == pytest.approx(0.5)  # no preference vector


# ---------------------------------------------------------------- template engine

def test_template_engine_variants_are_distinct():
    engine = TemplateAdapter().engine
    assert len(engine.templates) >= 40, "Section 6.5: about 40 templates"
    items = engine.generate({
        "purpose": "promote", "month_name": "November", "month": "2026-11",
        "season": "winter", "occasion": "Diwali", "audience": "families",
        "topic": "gifts", "keywords": ["lights"], "cta": "Shop now",
        "emoji_level": 1, "length_preset": "medium",
    }, arms=[("warm", "story-opening"), ("playful", "punchy"), ("witty", "question-hook")], seed=7)
    assert len(items) == 6
    assert len({i["text"] for i in items}) == 6
    for item in items:
        assert "Shop now" in item["text"] or "shop now" in item["text"].lower()
        assert "{" not in item["text"], "no unresolved slots"


# ---------------------------------------------------------------- tone classifier

def test_tone_classifier_trains_and_predicts(tone_clf, embedder):
    assert tone_clf.ready
    assert len(tone_clf.labels) == 8
    P = tone_clf.proba(embedder.embed(["Thank you so much for your kindness, we are grateful"]))
    assert P.shape == (1, 8)
    assert P.sum() == pytest.approx(1.0)
    assert tone_clf.predict(embedder.embed(["Last chance - act today, time is running out"]))[0] in tone_clf.labels


def test_confusion_matrix_shape(tone_clf, embedder):
    texts = ["We are writing to share an update", "Plot twist, this is fun",
             "Thank you for everything", "Last chance to act"]
    labels = ["professional", "playful", "grateful", "urgent"]
    preds = tone_clf.predict(embedder.embed(texts))
    cm = confusion_matrix(labels, preds, labels=tone_clf.labels)
    assert len(cm["matrix"]) == 8
    assert sum(sum(row) for row in cm["matrix"]) == 4


# ---------------------------------------------------------------- confidence / map

def test_embedding_map_payload(pipeline):
    result = pipeline.run(make_brief(), history=[], pref=PersonalizationState())
    m = result.embedding_map
    assert len(m["candidates"]) == 3
    assert m["intent"] is not None and "x" in m["intent"]


def test_regeneration_produces_a_linked_new_batch(pipeline):
    first = pipeline.run(make_brief(), history=[], pref=PersonalizationState())
    brief = make_brief(additional_instructions="make it shorter")
    second = pipeline.run(brief, history=[], pref=PersonalizationState())
    assert len(second.displayed) == 3
    assert second.displayed[0].text != first.displayed[0].text or \
           second.displayed[1].text != first.displayed[1].text
