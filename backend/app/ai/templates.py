"""Template engine - the availability fallback (Section 6.5 step 3).

A library of ~40 parameterised templates grouped by purpose and tone, with slots
for occasion, audience, keywords, CTA and season; each slot has 3-5 phrase
variants. Output still passes through embedding, scoring and MMR, so the user
receives ranked, novelty-checked options labelled "Template-generated".
"""
from __future__ import annotations

import json
import os
import random
import re

from app.ai.arms import STRUCTURES, TONE_LABELS, style_label
from app.ai.limits import preset_range

# <root>/backend/app/ai/templates.py -> <root>/data/templates.json
_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "templates.json",
)

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

_EMOJI = {
    "warm": [" ☕", " ✨"], "playful": [" ✨", " 🎉"], "witty": [" 😉", " ✨"],
    "professional": ["", ""], "formal": ["", ""], "inspirational": [" 🌱", " ✨"],
    "grateful": [" 🙏", " 💛"], "urgent": [" ⏰", " 🚨"],
}


def _fill(text: str, mapping: dict[str, str]) -> str:
    for _ in range(3):  # nested slots resolve within two passes
        before = text
        for key, value in mapping.items():
            text = text.replace("{" + key + "}", value)
        if text == before:
            break
    return text


class TemplateEngine:
    def __init__(self, templates: list[dict], slots: dict[str, list[str]]) -> None:
        self.templates = templates
        self.slots = slots

    @classmethod
    def load(cls, path: str | None = None) -> "TemplateEngine":
        path = path or _DATA_PATH
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return cls(data.get("templates", []), data.get("slots", {}))
        return cls([], {})

    # --- selection -------------------------------------------------------

    def _candidates_for(self, purpose: str, tone: str, structure: str, taken: set[str]) -> list[dict]:
        def score(t: dict) -> int:
            s = 0
            if t.get("purpose") == purpose:
                s += 4
            if t.get("tone") == tone:
                s += 2
            if t.get("structure") == structure:
                s += 1
            return s

        pool = [t for t in self.templates if t.get("id") not in taken]
        if not pool:
            pool = list(self.templates)
        pool.sort(key=score, reverse=True)
        top = [t for t in pool if score(t) == score(pool[0])] if pool else []
        return top or pool

    # --- generation ------------------------------------------------------

    def generate(self, brief: dict, arms: list[tuple[str, str]] | None = None,
                 per_arm: int = 2, seed: int | None = None) -> list[dict]:
        """Returns [{arm, text, template_id}] - two messages per arm, all distinct."""
        rng = random.Random(seed)
        purpose = (brief.get("purpose") or "inform").lower()
        arms = arms or [("warm", "story-opening"), ("playful", "punchy"), ("inspirational", "gratitude-first")]

        min_words, max_words = preset_range(brief.get("length_preset", "medium"))
        keywords = list(brief.get("keywords") or [])
        if not keywords:
            keywords = [brief.get("topic") or "our monthly update"]

        used_templates: set[str] = set()
        used_texts: set[str] = set()
        out: list[dict] = []

        for arm_idx, (tone, structure) in enumerate(arms, start=1):
            for k in range(per_arm):
                pool = self._candidates_for(purpose, tone, structure, used_templates)
                if not pool:
                    break
                template = pool[(k + len(used_templates)) % len(pool)]
                used_templates.add(template.get("id", ""))
                text = self._render(template, brief, tone, keywords, rng)
                # keep the batch distinct
                attempts = 0
                while text in used_texts and attempts < len(self.templates):
                    pool = [t for t in self._candidates_for(purpose, tone, structure, set())
                            if t.get("id") not in used_templates or attempts > 3]
                    template = pool[attempts % len(pool)] if pool else template
                    text = self._render(template, brief, tone, keywords, rng)
                    attempts += 1
                # pad with detail sentences until inside the word range
                text = self._pad(text, min_words, brief, keywords, rng)
                used_texts.add(text)
                out.append({"arm": arm_idx, "text": text, "template_id": template.get("id"),
                            "tone": tone, "structure": structure,
                            "style_label": style_label(tone, structure)})
        return out

    def _render(self, template: dict, brief: dict, tone: str, keywords: list[str],
                rng: random.Random) -> str:
        keyword = rng.choice(keywords)
        mapping = {
            # inner brief placeholders
            "month": brief.get("month_name") or brief.get("month", ""),
            "season": brief.get("season", ""),
            "occasion": brief.get("occasion") or brief.get("occasion_name") or f"{brief.get('month_name', '')} update",
            "audience": brief.get("audience", "our community"),
            "topic": brief.get("topic", ""),
            "keyword": keyword,
            "cta_text": brief.get("cta") or "get in touch",
            "platform": brief.get("platform", ""),
        }
        # slot phrase banks (variants with placeholders inside)
        for slot, variants in (self.slots or {}).items():
            if not variants:
                continue
            mapping[slot] = _fill(rng.choice(variants), mapping)

        text = _fill(template.get("text", ""), mapping)
        text = re.sub(r"\s{2,}", " ", text).strip()
        text = self._emoji(text, tone, int(brief.get("emoji_level", 1) or 0), rng)
        return text

    @staticmethod
    def _emoji(text: str, tone: str, level: int, rng: random.Random) -> str:
        if level <= 0:
            return re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text).strip()
        bank = _EMOJI.get(tone, ["", ""])
        if level == 1:
            pick = bank[0]
            return (text + pick) if pick else text
        picks = [e.strip() for e in bank if e.strip()]
        extra = " ".join(rng.sample(picks, min(2, len(picks))))
        return (text + " " + extra).strip() if extra else text

    @staticmethod
    def _pad(text: str, min_words: int, brief: dict, keywords: list[str],
             rng: random.Random) -> str:
        """Append detail sentences while short of the requested minimum (medium preset)."""
        def wc(s: str) -> int:
            return len(s.split())

        details = [
            "Pop in this week and see what we've prepared for {occasion}.",
            "{audience} can mention this post to pick their favourite option.",
            "We'll keep it simple: {keyword}, done properly, all {month}.",
            "Bring a friend - it's always better shared.",
            "We'd love to see you before the {season} is over.",
        ]
        attempts = 0
        while wc(text) < min_words - 2 and attempts < len(details):
            sentence = _fill(details[attempts % len(details)],
                             {"occasion": brief.get("occasion") or "the season",
                              "audience": brief.get("audience", "You"),
                              "keyword": rng.choice(keywords),
                              "month": brief.get("month_name") or "",
                              "season": brief.get("season", "season")})
            if sentence not in text:
                text = f"{text} {sentence}".strip()
            attempts += 1
        return text


def structure_options() -> list[str]:
    return list(STRUCTURES)


def label_for(tone: str) -> str:
    return TONE_LABELS.get(tone, tone.title())
