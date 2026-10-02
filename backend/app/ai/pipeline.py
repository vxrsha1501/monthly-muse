"""The hybrid generation pipeline (Section 4), steps 1-15.

Pure Python + NumPy: no database and no HTTP imports, so it can be unit-tested
with plain arrays and reused in evaluation notebooks. The caller (services layer)
supplies the embedder, tone classifier, history, personalization state and the
LLM generation service, and receives a fully scored, ranked, explained batch.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

from app.ai.arms import TONES, PURPOSES, arm_catalog, style_label, thompson_select
from app.ai.confidence import confidence_band, rank1_confidence
from app.ai.features import FEATURE_NAMES, build_feature_row, feature_dict
from app.ai.limits import acceptable_range, platform_limit
from app.ai.llm_adapters import GenerationService, PromptSpec, TemplateAdapter
from app.ai.mmr import mmr_select
from app.ai.prompt import build_prompt
from app.ai.scoring import DEFAULT_WEIGHTS, scores as dot_scores, weights_to_dict
from app.ai.similarity import (cosine_bands, max_similarity_to_history, pca_2d,
                               similarity_matrix, top_k_similar)
from app.ai.stats import length_suitability
from app.ai.text import (keyword_coverage, normalise, contains_banned, contains_cta,
                         extract_keywords, looks_english, tokenize, trim_to_sentences, word_count)

logger = logging.getLogger("monthlymuse.pipeline")

STAGES = ("validating", "context", "preprocessing", "retrieval", "arms", "prompt",
          "generating", "filtering", "embedding", "scoring", "ranking", "confidence", "done")

UI_STAGES = {
    "validating": "understand", "context": "understand", "preprocessing": "understand",
    "retrieval": "history", "arms": "history", "prompt": "generate", "generating": "generate",
    "filtering": "quality", "embedding": "quality", "scoring": "quality",
    "ranking": "rank", "confidence": "rank", "done": "done",
}


@dataclass
class Brief:
    """Validated generation input (the form's data, normalised)."""
    month: str = ""                     # "2026-11"
    month_name: str = ""
    topic: str = ""
    occasion: str = ""
    occasion_fact: str = ""
    season: str = ""
    audience: str = "our community"
    audience_description: str = ""
    tones: list[str] = field(default_factory=lambda: ["warm"])
    purpose: str = "inform"
    platform: str = "instagram"
    language: str = "en"
    length_preset: str = "medium"
    keywords: list[str] = field(default_factory=list)
    cta: str = ""
    voice_notes: str = ""
    banned_words: list[str] = field(default_factory=list)
    emoji_level: int = 1
    additional_instructions: str = ""
    lambda_mmr: float = 0.7

    def to_prompt_brief(self) -> dict:
        from app.ai.limits import preset_range
        lo, hi = preset_range(self.length_preset)
        return {
            "month": self.month, "month_name": self.month_name, "topic": self.topic,
            "occasion": self.occasion, "occasion_name": self.occasion or "this month",
            "occasion_fact": self.occasion_fact, "season": self.season,
            "audience": self.audience, "audience_name": self.audience,
            "audience_description": self.audience_description,
            "purpose": self.purpose, "platform": self.platform, "language": self.language,
            "min_words": lo, "max_words": hi, "length_preset": self.length_preset,
            "keywords": self.keywords, "cta": self.cta, "emoji_level": self.emoji_level,
        }


@dataclass
class HistoryItem:
    id: str
    text: str
    embedding: np.ndarray


@dataclass
class PersonalizationState:
    preference_vector: np.ndarray | None = None
    weights: np.ndarray = field(default_factory=lambda: DEFAULT_WEIGHTS.copy())
    length_mean: float | None = None
    length_std: float | None = None
    arm_stats: dict[tuple[str, str], tuple[float, float]] = field(default_factory=dict)


@dataclass
class Candidate:
    text: str
    arm_idx: int = 1
    tone: str = "warm"
    structure: str = "story-opening"
    style_label: str = ""
    is_fallback: bool = False
    word_count: int = 0
    char_count: int = 0
    features: dict = field(default_factory=dict)
    score: float = 0.0
    rank: int | None = None
    p_rank1: float | None = None
    confidence: str = "medium"
    nearest_history_id: str | None = None
    nearest_cosine: float | None = None
    similarity_band: str = "Fresh"
    status: str = "shown"               # shown | filtered
    filter_reason: str | None = None
    embedding: np.ndarray | None = field(default=None, repr=False)
    explain: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    displayed: list[Candidate]
    filtered: list[Candidate]
    request: dict
    retrieval: dict
    arms: list[tuple[str, str]]
    embedding_map: dict
    warnings: list[str] = field(default_factory=list)


def _intent_text(brief: Brief, keywords: list[str]) -> str:
    parts = [f"Monthly {brief.platform} post for {brief.audience}"]
    if brief.topic:
        parts.append(f"about {brief.topic}")
    if brief.occasion:
        parts.append(f"occasion: {brief.occasion}")
    parts.append(f"purpose: {brief.purpose}")
    if keywords:
        parts.append("keywords: " + ", ".join(keywords))
    if brief.cta:
        parts.append(f"CTA: {brief.cta}")
    return ", ".join(parts) + "."


def _validate(brief: Brief) -> list[str]:
    warnings: list[str] = []
    if not brief.tones:
        brief.tones = ["warm"]
        warnings.append("No tone requested; defaulted to Warm.")
    bad = [t for t in brief.tones if t not in TONES]
    if bad:
        brief.tones = [t for t in brief.tones if t in TONES] or ["warm"]
        warnings.append(f"Unknown tones removed: {', '.join(bad)}.")
    brief.tones = brief.tones[:2]
    if brief.purpose not in PURPOSES:
        brief.purpose = "inform"
    if len(brief.keywords) > 6:
        brief.keywords = brief.keywords[:6]
        warnings.append("Keywords limited to 6.")
    if len(brief.additional_instructions or "") > 500:
        brief.additional_instructions = brief.additional_instructions[:500]
        warnings.append("Additional instructions truncated to 500 characters.")
    return warnings


class Pipeline:
    def __init__(self, *, embedder, tone_classifier, generator: GenerationService | None = None,
                 seed: int = 42) -> None:
        self.embedder = embedder
        self.tone_classifier = tone_classifier
        self.generator = generator or GenerationService()
        self.seed = seed

    # ------------------------------------------------------------------ run
    def run(self, brief: Brief, history: list[HistoryItem], pref: PersonalizationState, *,
            stage_cb=None, trigger: str = "manual") -> PipelineResult:
        started = time.perf_counter()
        warnings: list[str] = []

        def stage(name: str) -> None:
            if stage_cb:
                stage_cb(name)

        # [1] validate + normalise
        stage("validating")
        warnings += _validate(brief)

        # [2] context assembly (season/occasion are resolved by the caller)
        stage("context")
        from app.ai.text import extract_keywords as _extract
        auto_keywords = _extract(f"{brief.topic} {brief.occasion} {brief.occasion_fact}", k=6)
        keywords = list(dict.fromkeys([*(brief.keywords or []), *auto_keywords]))[:8]

        # [3] NLP preprocessing + keyword extraction
        stage("preprocessing")
        coverage_keywords = brief.keywords or keywords[:4]

        # [4] intent vector
        intent_text = _intent_text(brief, coverage_keywords)
        q = self.embedder.embed([intent_text])[0]

        # [5] retrieval (RAG)
        stage("retrieval")
        H = np.vstack([h.embedding for h in history]) if history else np.zeros((0, len(q)), dtype=np.float32)
        exemplar_idx = top_k_similar(q, H, 3)
        recent_idx = list(range(min(6, len(history))))     # history is newest-first
        exemplars = [history[i].text for i in exemplar_idx]
        recent = [history[i].text for i in recent_idx]

        # [6] style-arm selection (Thompson sampling)
        stage("arms")
        rng = np.random.default_rng(self.seed)
        arms = thompson_select(list(brief.tones), pref.arm_stats, rng=rng, n_arms=3)

        # [7] prompt construction
        stage("prompt")
        prompt_brief = brief.to_prompt_brief()
        system, user = build_prompt(
            brief=prompt_brief, arms=arms, exemplars=exemplars, recent=recent,
            voice_notes=brief.voice_notes, banned_words=brief.banned_words,
            additional_instructions=brief.additional_instructions,
        )
        spec = PromptSpec(system=system, user=user, brief=prompt_brief)

        # [8] one LLM call for six candidates (fallback: templates)
        stage("generating")
        llm_started = time.perf_counter()
        result = self.generator.generate(spec)
        llm_ms = int((time.perf_counter() - llm_started) * 1000)
        raw = result.candidates[:6]
        is_fallback = result.provider == "template"
        if not raw:
            raw = TemplateAdapter().complete(spec).candidates[:6]
            is_fallback = True

        # map arm index -> (tone, structure)
        def arm_of(idx: int) -> tuple[str, str]:
            return arms[min(max(idx, 1), len(arms)) - 1]

        # [9] post-processing + hard constraints
        stage("filtering")
        survivors: list[Candidate] = []
        filtered: list[Candidate] = []
        history_norm = {normalise(h.text) for h in history}

        def constrain(text: str, arm_idx: int, extra: dict | None = None) -> None:
            c = self._make_candidate(text, arm_idx, arm_of, is_fallback, brief)
            reason = self._hard_constraint_reason(c, brief, history_norm, keywords=coverage_keywords)
            if reason:
                c.status = "filtered"
                c.filter_reason = reason
                filtered.append(c)
            else:
                survivors.append(c)

        for item in raw:
            text = (item.get("text") or "").strip()
            if text:
                constrain(text, int(item.get("arm", 1)))

        # top-up if fewer than three survived: one retry, then the template engine
        if len(survivors) < 3:
            retry_spec = PromptSpec(system=system, user=user + "\nWrite completely different messages.",
                                    brief=prompt_brief)
            try:
                topup = self.generator.generate(retry_spec)
                for item in topup.candidates:
                    if len(survivors) >= 6:
                        break
                    text = (item.get("text") or "").strip()
                    if text and all(normalise(text) != normalise(s.text) for s in survivors):
                        constrain(text, int(item.get("arm", 1)))
            except Exception as exc:  # pragma: no cover
                logger.warning("topup_failed: %s", exc)
        if len(survivors) < 3:
            for item in TemplateAdapter().complete(spec).candidates:
                if len(survivors) >= 6:
                    break
                text = (item.get("text") or "").strip()
                if text and all(normalise(text) != normalise(s.text) for s in survivors):
                    c = self._make_candidate(text, int(item.get("arm", 1)), arm_of, True, brief)
                    if not self._hard_constraint_reason(c, brief, history_norm, keywords=coverage_keywords):
                        survivors.append(c)

        # [10] embeddings -> similarity matrices
        stage("embedding")
        if survivors:
            E = np.vstack([c.embedding for c in survivors])
        else:
            E = np.zeros((0, len(q)), dtype=np.float32)
        max_cos, nearest_idx = max_similarity_to_history(E, H) if len(survivors) else (np.zeros(0), np.array([], int))
        intent_sims = (E @ q) if len(survivors) else np.zeros(0)

        # [11] features and [12] scores
        stage("scoring")
        F = np.zeros((len(survivors), len(FEATURE_NAMES)))
        tone_probs = self.tone_classifier.proba(E) if len(survivors) else np.zeros((0, 1))
        labels = self.tone_classifier.labels or []
        for i, c in enumerate(survivors):
            c.nearest_cosine = float(max_cos[i]) if len(max_cos) else 0.0
            if nearest_idx.size and nearest_idx[i] >= 0:
                c.nearest_history_id = history[int(nearest_idx[i])].id
            c.similarity_band = cosine_bands(c.nearest_cosine or 0.0)
            cov = keyword_coverage(coverage_keywords, c.text)
            if labels and c.tone in labels:
                p_tone = float(tone_probs[i, labels.index(c.tone)])
            elif labels:
                p_tone = float(np.max(tone_probs[i]))
            else:
                p_tone = 0.5
            alpha, beta = pref.arm_stats.get((c.tone, c.structure), (1.0, 1.0))
            row = build_feature_row(
                cos_intent=float(intent_sims[i]) if intent_sims.size else 0.0,
                keyword_cov=cov, p_intended=p_tone, e=c.embedding,
                u=pref.preference_vector, max_cos_history=c.nearest_cosine or 0.0,
                word_count=c.word_count,
                length_mean=pref.length_mean or _preset_mid(brief),
                length_std=pref.length_std, alpha=alpha, beta=beta,
            )
            F[i] = row
            c.features = feature_dict(row)
            c.explain["keyword_coverage"] = round(cov, 3)
            c.explain["intent_cosine"] = round(float(intent_sims[i]) if intent_sims.size else 0.0, 3)
            c.explain["tone_probability"] = round(p_tone, 3)

        s = dot_scores(F, pref.weights) if len(survivors) else np.zeros(0)
        for i, c in enumerate(survivors):
            c.score = float(s[i])

        # near-duplicate removal inside the batch (cos >= 0.90, keep the higher score)
        if len(survivors) > 1:
            S_cc = similarity_matrix(E, E)
            drop: set[int] = set()
            for i in range(len(survivors)):
                if i in drop:
                    continue
                for j in range(i + 1, len(survivors)):
                    if j in drop:
                        continue
                    if S_cc[i, j] >= 0.90:
                        loser = i if survivors[i].score <= survivors[j].score else j
                        drop.add(loser)
            for idx in sorted(drop, reverse=True):
                c = survivors.pop(idx)
                c.status = "filtered"
                c.filter_reason = "near_duplicate"
                filtered.append(c)
            F = np.delete(F, list(drop), axis=0) if drop else F
            s = np.delete(s, list(drop)) if drop else s
            E = np.delete(E, list(drop), axis=0) if drop else E
        else:
            S_cc = similarity_matrix(E, E)

        # [13] MMR top-3 (prefer three distinct style arms)
        stage("ranking")
        groups = [c.arm_idx for c in survivors]
        order = mmr_select(s, E, k=3, lam=float(brief.lambda_mmr or 0.7),
                           groups=groups) if len(survivors) else []
        displayed = []
        for rank, idx in enumerate(order, start=1):
            c = survivors[idx]
            c.rank = rank
            displayed.append(c)

        # [14] Monte-Carlo confidence + explanation payload + PCA coordinates
        stage("confidence")
        p_rank1 = rank1_confidence(F, pref.weights, seed=self.seed) if len(survivors) else np.zeros(0)
        for i, c in enumerate(survivors):
            c.p_rank1 = float(p_rank1[i]) if p_rank1.size else None
            c.confidence = confidence_band(c.p_rank1) if c.p_rank1 is not None else "low"
            c.explain["reasons"] = self._reasons(c, pref)
        for c in filtered:
            c.explain.setdefault("reasons", [])

        # embedding map: displayed + intent + history points
        map_points = self._embedding_map(displayed, q, history)

        total_ms = int((time.perf_counter() - started) * 1000)
        stage("done")
        request_meta = {
            "intent_text": intent_text,
            "intent_embedding": q,
            "provider": result.provider,
            "model": result.model,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "cost_usd": result.cost_usd,
            "latency_ms": total_ms,
            "llm_latency_ms": llm_ms,
            "weights_snapshot": weights_to_dict(pref.weights),
            "is_fallback": is_fallback,
            "trigger": trigger,
        }
        return PipelineResult(
            displayed=displayed, filtered=filtered, request=request_meta,
            retrieval={"exemplar_ids": [history[i].id for i in exemplar_idx],
                       "recent_ids": [history[i].id for i in recent_idx],
                       "n_history": len(history), "exemplars": exemplars, "recent": recent},
            arms=arms, embedding_map=map_points, warnings=warnings,
        )

    # ------------------------------------------------------------ helpers
    def _make_candidate(self, text: str, arm_idx: int, arm_of, is_fallback: bool,
                        brief: Brief) -> Candidate:
        tone, structure = arm_of(arm_idx)
        text = normalise(text)
        lo, hi = acceptable_range(brief.length_preset)
        if word_count(text) > hi:
            text = trim_to_sentences(text, hi)
        return Candidate(
            text=text, arm_idx=arm_idx, tone=tone, structure=structure,
            style_label=style_label(tone, structure), is_fallback=is_fallback,
            word_count=word_count(text), char_count=len(text),
            embedding=self.embedder.embed([text])[0],   # e_c, computed locally (free)
        )

    @staticmethod
    def _hard_constraint_reason(c: Candidate, brief: Brief, history_norm: set[str],
                                keywords: list[str]) -> str | None:
        lo, hi = acceptable_range(brief.length_preset)
        if c.char_count > platform_limit(brief.platform):
            return "over_platform_limit"
        if c.word_count > hi:
            return "too_long"
        if c.word_count < lo:
            return "too_short"
        if brief.language == "en" and not looks_english(c.text):
            return "language"
        if contains_banned(c.text, brief.banned_words):
            return "banned_word"
        if brief.cta and not contains_cta(c.text, brief.cta):
            return "missing_cta"
        if normalise(c.text) in history_norm:
            return "duplicate_of_history"
        return None

    @staticmethod
    def _reasons(c: Candidate, pref: PersonalizationState) -> list[str]:
        """Plain-language top-2 reasons behind the score (Section 4.4)."""
        if not c.features:
            return []
        contributions = {name: pref.weights[i] * c.features[name]
                         for i, name in enumerate(FEATURE_NAMES)}
        top = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)[:2]
        templates = {
            "relevance": lambda v: f"Strong match to your brief ({round(c.explain.get('intent_cosine', 0) * 100)}% to intent)",
            "tone": lambda v: f"Delivers your {c.tone} tone ({round(v and c.features['tone'] * 100)}%)",
            "personalization": lambda v: f"Fits your usual voice ({round(c.features['personalization'] * 100)}%)",
            "novelty": lambda v: f"{round((c.nearest_cosine or 0) * 100)}% different from your nearest past post",
            "length": lambda v: f"Fits your preferred length ({c.word_count} words)",
            "history": lambda v: f"Your {c.tone} style has been selected before",
        }
        return [templates[name](value) for name, value in top if name in templates]

    def _embedding_map(self, displayed: list[Candidate], q: np.ndarray,
                       history: list[HistoryItem]) -> dict:
        """2-D PCA scatter: candidates + intent + recent history (Section 3.3)."""
        pts: list[np.ndarray] = []
        cand_pos: list[int] = []
        for c in displayed:
            cand_pos.append(len(pts))
            pts.append(c.embedding)
        intent_pos = len(pts)
        pts.append(q)
        hist_from = len(pts)
        for h in history[:8]:
            pts.append(h.embedding)
        if not pts:
            return {"candidates": [], "intent": None, "history": []}
        M = np.vstack(pts)
        xy = pca_2d(M)
        return {
            "candidates": [{"rank": c.rank, "x": float(xy[i, 0]), "y": float(xy[i, 1])}
                           for i, c in zip(cand_pos, displayed)],
            "intent": {"x": float(xy[intent_pos, 0]), "y": float(xy[intent_pos, 1])},
            "history": [{"x": float(xy[j, 0]), "y": float(xy[j, 1])}
                        for j in range(hist_from, len(pts))],
        }


def _preset_mid(brief: Brief) -> float:
    from app.ai.limits import preset_range
    lo, hi = preset_range(brief.length_preset)
    return (lo + hi) / 2


def cosine_band_label(max_cos: float) -> str:
    return cosine_bands(max_cos)


__all__ = ["Brief", "Candidate", "HistoryItem", "PersonalizationState", "Pipeline",
           "PipelineResult", "STAGES", "UI_STAGES"]
