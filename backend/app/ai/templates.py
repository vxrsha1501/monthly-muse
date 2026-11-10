"""Template engine - the availability fallback (Section 6.5 step 3).

A library of ~60 parameterised templates grouped by purpose and tone, with slots
for occasion, audience, keywords, CTA and season; each slot has 3-5 phrase
variants. Output still passes through embedding, scoring and MMR, so the user
receives ranked, novelty-checked options labelled "Template-generated".

Two invariants this engine guarantees (audit Task 2 / review P3, P4):

* **Occasion gating** - templates that mention ``{occasion}`` are only ever
  rendered when the brief carries a *decided* occasion (the services layer has
  already run the relevance + date gates, see app/ai/occasion.py).  With no
  occasion, only occasion-free templates render, so an irrelevant or past
  holiday can never leak into the text.
* **Coherent text** - user CTA punctuation is normalised before slot fill
  (root fix for "next 5 days.."), the emoji is appended after padding (root fix
  for emoji landing mid-text), and a defensive ``_sanitise`` pass collapses any
  duplicated words/sentences or stacked punctuation that upstream data could
  still introduce.
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

# --- defensive text repair ---------------------------------------------------
_DUP_SENTENCE_RE = re.compile(r"\b(\w+)([.!?])\s+\1\b", re.I)   # "today. today."
_DUP_WORD_RE = re.compile(r"\b(\w{3,})\s+\1\b", re.I)           # "our our"
_TRAILING_PUNCT_RE = re.compile(r"\s*[,;:\-]\s*$")


def _fill(text: str, mapping: dict[str, str]) -> str:
    for _ in range(3):  # nested slots resolve within two passes
        before = text
        for key, value in mapping.items():
            text = text.replace("{" + key + "}", value)
        if text == before:
            break
    return text


def _sanitise(text: str) -> str:
    """Defensive sweep - upstream defects are fixed at the root; this only
    guarantees no duplicated fragments or malformed punctuation ship to users."""
    text = re.sub(r"\s+", " ", text).strip()
    text = _DUP_SENTENCE_RE.sub(r"\1\2", text)         # "today. today." -> "today."
    text = _DUP_WORD_RE.sub(r"\1", text)               # "our our" -> "our"
    text = re.sub(r"\.\s+\.", ".", text)               # ". ." -> "."
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)     # ".." -> "." (keeps "...")
    text = re.sub(r"([!?])\s*\1+", r"\1", text)        # "!!" -> "!"
    text = _TRAILING_PUNCT_RE.sub(".", text)           # dangling "," / "-" -> "."
    text = re.sub(r"\.{4,}", "...", text)              # cap dot runs
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)       # space before punctuation
    return text.strip()


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

    @staticmethod
    def _uses_occasion(template: dict) -> bool:
        return "{occasion" in (template.get("text") or "")

    def _available(self, brief: dict) -> list[dict]:
        """Occasion templates only when the occasion actually survived the
        relevance + date gates (set by the services layer, app/ai/occasion.py).

        - ``occasion_status == "upcoming" | "today"``: the occasion is decided,
          so occasion-bearing templates are eligible (with the future_only
          exclusion when it has already passed).
        - ``occasion_status == "past"``: only non-future-phrased occasion
          templates may render (a retrospective); future-phrased ones are
          forbidden so a passed occasion is never sold as upcoming.
        - ``occasion_status == ""`` (no occasion / dropped by the gate): only
          occasion-free templates render.  This is what stops an unrelated
          holiday (Gandhi Jayanti on an unrelated hiring post) from leaking
          into the generated message, and what keeps calendar events out when
          the user did not select one.
        """
        status = brief.get("occasion_status") or ""
        free = [t for t in self.templates if not self._uses_occasion(t)]
        if status in ("upcoming", "today"):
            pool = [t for t in self.templates if self._uses_occasion(t)]
        elif status == "past":
            pool = [t for t in self.templates
                    if self._uses_occasion(t) and not t.get("future_only")]
        else:
            pool = free
        # never widen back to occasion templates when no occasion was decided
        return pool or free or list(self.templates)

    def _candidates_for(self, purpose: str, tone: str, structure: str, taken: set[str],
                        source: list[dict] | None = None) -> list[dict]:
        source = self.templates if source is None else source

        def score(t: dict) -> int:
            s = 0
            if t.get("purpose") == purpose:
                s += 4
            if t.get("tone") == tone:
                s += 2
            if t.get("structure") == structure:
                s += 1
            return s

        pool = [t for t in source if t.get("id") not in taken]
        if not pool:
            pool = list(source)
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

        available = self._available(brief)
        used_templates: set[str] = set()
        used_texts: set[str] = set()
        out: list[dict] = []

        for arm_idx, (tone, structure) in enumerate(arms, start=1):
            for k in range(per_arm):
                pool = self._candidates_for(purpose, tone, structure, used_templates, available)
                if not pool:
                    break
                template = pool[(k + len(used_templates)) % len(pool)]
                used_templates.add(template.get("id", ""))
                text = self._render(template, brief, tone, keywords, rng)
                # keep the batch distinct
                attempts = 0
                while text in used_texts and attempts < len(available):
                    pool = [t for t in self._candidates_for(purpose, tone, structure, set(), available)
                            if t.get("id") not in used_templates or attempts > 3]
                    template = pool[attempts % len(pool)] if pool else template
                    text = self._render(template, brief, tone, keywords, rng)
                    attempts += 1
                # pad with detail sentences until inside the word range, then decorate
                text = self._pad(text, min_words, brief, keywords, rng)
                text = _sanitise(text)
                text = self._emoji(text, tone, int(brief.get("emoji_level", 1) or 0), rng)
                used_texts.add(text)
                out.append({"arm": arm_idx, "text": text, "template_id": template.get("id"),
                            "tone": tone, "structure": structure,
                            "style_label": style_label(tone, structure)})
        return out

    def _render(self, template: dict, brief: dict, tone: str, keywords: list[str],
                rng: random.Random) -> str:
        keyword = rng.choice(keywords)
        # Root fix for "…next 5 days.." / "…5 days. today.": user CTA text may
        # already end in punctuation - strip it before any variant appends one.
        cta_text = re.sub(r"[\s.,;:!?…\"')\]]+$", "", str(brief.get("cta") or "")) or "get in touch"
        mapping = {
            # inner brief placeholders
            "month": brief.get("month_name") or brief.get("month", ""),
            "season": brief.get("season", ""),
            "occasion": brief.get("occasion") or "",
            "audience": brief.get("audience", "our community"),
            "topic": brief.get("topic", ""),
            "keyword": keyword,
            "cta_text": cta_text,
            "platform": brief.get("platform", ""),
        }
        # slot phrase banks (variants with placeholders inside)
        for slot, variants in (self.slots or {}).items():
            if not variants:
                continue
            if slot == "cta":
                mapping[slot] = self._cta_variant(variants, mapping, rng)
                continue
            mapping[slot] = _fill(rng.choice(variants), mapping)

        text = _fill(template.get("text", ""), mapping)
        return _sanitise(text)

    @staticmethod
    def _cta_variant(variants: list[str], mapping: dict[str, str], rng: random.Random) -> str:
        """Choose a CTA variant; if it would duplicate the CTA's own final word
        ("…today" + " today."), fall back to the plain variant."""
        filled = _fill(rng.choice(variants), mapping)
        if _DUP_WORD_RE.search(filled) or _DUP_SENTENCE_RE.search(filled):
            plain = "{cta_text}." if "{cta_text}." in variants else variants[0]
            filled = _fill(plain, mapping)
        return filled

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

        occasion = str(brief.get("occasion") or "").strip()
        details = []
        if occasion:
            details.append(f"Pop in this week and see what we've prepared for {occasion}.")
        else:
            details.append(f"Pop in this week and see what we've prepared for "
                           f"{brief.get('month_name') or 'this month'}.")
        details += [
            "{audience} can mention this post to pick their favourite option.",
            "We'll keep it simple: {keyword}, done properly, all {month}.",
            "Bring a friend - it's always better shared.",
            "We'd love to see you before the {season} is over.",
        ]
        attempts = 0
        while wc(text) < min_words - 2 and attempts < len(details):
            sentence = _fill(details[attempts % len(details)],
                             {"occasion": occasion or "the season",
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


__all__ = ["TemplateEngine", "structure_options", "label_for", "_sanitise"]
