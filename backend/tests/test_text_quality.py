"""Regression tests: generated text must stay coherent (audit Task 2 / review P4).

Root causes fixed upstream:
  * user CTA punctuation normalised before slot fill  ("...5 days.." / "today. today.")
  * emoji appended after padding (was landing mid-text)
  * keyword_ref appositional variants removed (broken NP grammar)
  * occasion templates only render when the occasion gates let one through

``_sanitise`` is the defensive backstop - these tests prove BOTH layers.
"""
from __future__ import annotations

import re

import pytest

from app.ai.embedder import HashingEmbedder
from app.ai.llm_adapters import GenerationService, TemplateAdapter
from app.ai.pipeline import Brief, HistoryItem, PersonalizationState, Pipeline
from app.ai.templates import TemplateEngine, _sanitise
from app.ai.tone import ToneClassifier

_DOUBLED_WORD = re.compile(r"\b(\w+) \1\b", re.I)
_DOUBLED_PERIOD = re.compile(r"\.\.|\. \.")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+[,.;]")


def _defects(text: str) -> list[str]:
    hits = []
    if _DOUBLED_WORD.search(text):
        hits.append(f"duplicated word: {text!r}")
    if _DOUBLED_PERIOD.search(text):
        hits.append(f"stacked period: {text!r}")
    if _SPACE_BEFORE_PUNCT.search(text):
        hits.append(f"space before punctuation: {text!r}")
    if "{" in text or "}" in text:
        hits.append(f"unresolved slot: {text!r}")
    return hits


BASE_BRIEF = {
    "month": "2026-11", "month_name": "November", "topic": "Part-time barista hiring",
    "occasion": "", "occasion_status": "", "season": "post-monsoon",
    "audience": "our community", "purpose": "inform", "platform": "instagram",
    "language": "en", "length_preset": "medium",
    "keywords": ["hiring", "part-time", "barista"],
    "cta": "Visit the store for interviews in the next 5 days.", "emoji_level": 1,
}

ARMS = [("warm", "story-opening"), ("playful", "punchy"), ("inspirational", "gratitude-first")]


# ------------------------------------------------------------ sanitise unit
def test_sanitise_collapses_duplicated_fragments():
    assert _sanitise("today. today.") == "today."
    assert _sanitise("our our community community") == "our community"
    assert _sanitise("Welcome.  . Thanks") == "Welcome. Thanks"


def test_sanitise_fixes_punctuation_malformations():
    assert _sanitise("in the next 5 days..") == "in the next 5 days."
    assert _sanitise("in the next 5 days. today.") == "in the next 5 days. today."
    assert _sanitise("See you soon , friend ; really") == "See you soon, friend; really"
    assert _sanitise("trailing comma,") == "trailing comma."


def test_sanitise_keeps_intended_ellipsis():
    assert "..." in _sanitise("it started quietly... then everything changed")


# ------------------------------------------------------------ engine sweeps
@pytest.mark.parametrize("cta", [
    "Visit the store for interviews in the next 5 days.",
    "Visit us this weekend",
    "Book now!",
    "Follow us today.",
    "Read more...",
])
def test_cta_punctuation_never_produces_malformed_text(cta):
    engine = TemplateAdapter().engine
    brief = dict(BASE_BRIEF, cta=cta)
    for seed in range(30):
        for item in engine.generate(brief, arms=ARMS, per_arm=2, seed=seed):
            assert not _defects(item["text"]), item


def test_full_sweep_no_duplicate_fragments_across_templates():
    engine = TemplateAdapter().engine
    checked = 0
    for seed in range(40):
        for item in engine.generate(dict(BASE_BRIEF), arms=ARMS, per_arm=2, seed=seed):
            checked += 1
            assert not _defects(item["text"]), item
            # emoji (if any) never lands mid-text
            assert not re.search(r"[☀-➿\U0001F300-\U0001FAFF]\s+\w", item["text"].split("]")[-1]), item["text"]
    assert checked == 240


def test_no_occasion_brief_never_mentions_an_occasion():
    engine = TemplateAdapter().engine
    for seed in range(40):
        for item in engine.generate(dict(BASE_BRIEF), arms=ARMS, per_arm=2, seed=seed):
            assert "Gandhi" not in item["text"]
            assert "Diwali" not in item["text"]
            # occasion-free templates must not even allude to a calendar event
            assert "{occasion" not in item["text"]


def test_occasion_brief_renders_occasion_templates_only():
    engine = TemplateAdapter().engine
    brief = dict(BASE_BRIEF, occasion="Diwali", occasion_status="upcoming",
                 topic="Diwali special menu", keywords=["diwali", "sweets"])
    seen = set()
    for seed in range(30):
        for item in engine.generate(brief, arms=ARMS, per_arm=2, seed=seed):
            seen.add(item["template_id"])
            assert "Diwali" in item["text"], item
            assert not _defects(item["text"]), item
    assert len(seen) >= 5


def test_past_occasion_never_uses_future_phrasing_templates():
    engine = TemplateAdapter().engine
    future_ids = {t["id"] for t in engine.templates if t.get("future_only")}
    assert future_ids, "future_only tags must exist in templates.json"
    brief = dict(BASE_BRIEF, occasion="Diwali", occasion_status="past",
                 topic="Diwali thank-you post", keywords=["diwali", "thanks"])
    used = set()
    for seed in range(60):
        for item in engine.generate(brief, arms=ARMS, per_arm=2, seed=seed):
            used.add(item["template_id"])
            assert item["template_id"] not in future_ids, item
    assert used and not (used & future_ids)


def test_emoji_zero_level_strips_and_two_level_appends_at_end():
    engine = TemplateAdapter().engine
    quiet = dict(BASE_BRIEF, emoji_level=0)
    loud = dict(BASE_BRIEF, emoji_level=2)
    for seed in range(10):
        for item in engine.generate(quiet, arms=ARMS, per_arm=2, seed=seed):
            assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", item["text"])
        for item in engine.generate(loud, arms=ARMS, per_arm=2, seed=seed):
            tail = item["text"][-4:]
            assert any(ch for ch in tail if ord(ch) > 0x2000) or item["text"].endswith(".")


# ------------------------------------------------------------ full pipeline
@pytest.fixture(scope="module")
def pipeline():
    embedder = HashingEmbedder(384)
    return Pipeline(embedder=embedder, tone_classifier=ToneClassifier(),
                    generator=GenerationService(TemplateAdapter()), seed=42)


def _brief(**over) -> Brief:
    data = dict(
        month="2026-11", month_name="November", topic="Part-time barista hiring",
        occasion="", occasion_status="", season="post-monsoon", audience="our community",
        tones=["warm", "playful"], purpose="announce", platform="instagram",
        language="en", length_preset="medium", keywords=["hiring", "part-time", "barista"],
        cta="Visit the store for interviews in the next 5 days.", emoji_level=1,
    )
    data.update(over)
    return Brief(**data)


def test_pipeline_candidates_are_clean_and_occasion_free(pipeline):
    result = pipeline.run(_brief(), history=[], pref=PersonalizationState())
    assert len(result.displayed) == 3
    for cand in [*result.displayed, *result.filtered]:
        assert not _defects(cand.text), cand.text
        assert "Gandhi" not in cand.text and "Diwali" not in cand.text


def test_pipeline_with_relevant_occasion_is_clean_too(pipeline):
    result = pipeline.run(
        _brief(topic="Diwali special menu", occasion="Diwali", occasion_status="upcoming",
               purpose="promote", keywords=["diwali", "sweets", "menu"]),
        history=[], pref=PersonalizationState())
    for cand in result.displayed:
        assert not _defects(cand.text), cand.text


def test_regenerated_batches_stay_clean(pipeline):
    for _ in range(3):
        result = pipeline.run(_brief(), history=[], pref=PersonalizationState())
        for cand in result.displayed:
            assert not _defects(cand.text), cand.text
