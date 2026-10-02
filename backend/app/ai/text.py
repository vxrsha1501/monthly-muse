"""NLP preprocessing: normalisation, tokenisation, keywords, overlap checks.

spaCy is optional (blueprint lists it); this module ships a lightweight
pure-Python implementation so the pipeline runs anywhere. Embedding models
receive raw text - aggressive preprocessing only happens on the sparse branch.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

STOPWORDS = frozenset("""
a an the and or but if then so as of at by for with about into over after before
to from up down out off on in is are was were be been being am do does did doing
have has had having it its this that these those i you he she we they them his
her their our your my me us what which who whom when where why how all any both
each few more most other some such no nor not only own same than too very can
will just should now also into upon within without via per etc one two three
""".split())

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")
_SENT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD_RE = re.compile(r"[a-zA-Z0-9']+")

# very small rule-based lemmatiser (plural / tense endings)
_SUFFIX_RULES = (
    ("ies", "y"), ("sses", "ss"), ("ches", "ch"), ("shes", "sh"), ("xes", "x"),
    ("ses", "s"), ("s", ""), ("ing", ""), ("ed", ""), ("ly", ""), ("er", ""), ("est", ""),
)


def normalise(text: str) -> str:
    """Unicode NFKC, strip control characters, collapse whitespace (Section 4 step 1)."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def lemma(token: str) -> str:
    token = token.lower()
    if len(token) <= 3 or token in STOPWORDS:
        return token
    for suffix, repl in _SUFFIX_RULES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)] + repl
    return token


def content_tokens(text: str) -> list[str]:
    return [lemma(t) for t in tokenize(text) if t not in STOPWORDS and len(t) > 2]


def sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_RE.split(text or "") if p and p.strip()]
    return parts


def first_sentence(text: str) -> str:
    s = sentences(text)
    return s[0] if s else (text or "")


def word_count(text: str) -> int:
    return len(tokenize(text))


def char_count(text: str) -> int:
    return len(text or "")


def extract_keywords(text: str, k: int = 6) -> list[str]:
    """TF-based keyword extraction with stopword removal (YAKE/TF-IDF stand-in)."""
    tokens = content_tokens(text)
    counts = Counter(tokens)
    if not counts:
        return []
    # length-normalised tf, slight boost for longer (more specific) words
    total = sum(counts.values())
    scored = sorted(counts.items(), key=lambda kv: (kv[1] / total) * (1 + 0.03 * len(kv[0])), reverse=True)
    return [w for w, _ in scored[:k]]


def keyword_coverage(keywords: list[str], text: str) -> float:
    """Fraction of keywords present as lemma matches in the text."""
    if not keywords:
        return 1.0
    text_lemmas = {lemma(t) for t in tokenize(text)}
    hits = sum(1 for kw in keywords if lemma(kw.strip().lower()) in text_lemmas
               or kw.strip().lower() in (text or "").lower())
    return hits / len(keywords)


def ngram_jaccard(a: str, b: str, n: int = 4) -> float:
    """Jaccard overlap of character n-grams - catches copy-paste phrasing (Section 4.3)."""
    def grams(s: str) -> set[str]:
        s = re.sub(r"\s+", " ", (s or "").lower()).strip()
        return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}

    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def opening_line_repeats(a: str, b: str) -> bool:
    fa, fb = first_sentence(a).lower().strip(), first_sentence(b).lower().strip()
    return bool(fa) and fa == fb


def looks_english(text: str) -> bool:
    """Lightweight language gate (langdetect/spaCy optional)."""
    tokens = tokenize(text)
    if not tokens:
        return False
    ascii_words = sum(1 for t in tokens if t.isascii())
    return ascii_words / len(tokens) >= 0.90


def contains_banned(text: str, banned: list[str]) -> bool:
    low = (text or "").lower()
    low_lemmas = " ".join(lemma(t) for t in tokenize(low))
    for word in banned or []:
        w = (word or "").strip().lower()
        if not w:
            continue
        if w in low or lemma(w) in low_lemmas:
            return True
    return False


def contains_cta(text: str, cta: str | None) -> bool:
    if not cta:
        return True
    cta_tokens = [t for t in tokenize(cta) if t not in STOPWORDS]
    if not cta_tokens:
        return True
    text_lemmas = {lemma(t) for t in tokenize(text)}
    hits = sum(1 for t in cta_tokens if lemma(t) in text_lemmas)
    # key phrase present (>= 60% of the CTA's content words)
    return hits / len(cta_tokens) >= 0.6


def trim_to_sentences(text: str, max_words: int) -> str:
    """Discard trailing sentences while over the word budget (Section 4 step 9)."""
    if word_count(text) <= max_words:
        return text
    parts = sentences(text)
    kept: list[str] = []
    count = 0
    for part in parts:
        wc = word_count(part)
        if count + wc > max_words and kept:
            break
        kept.append(part)
        count += wc
    return " ".join(kept)


def edit_distance_tokens(a: str, b: str) -> int:
    """Token-level Levenshtein distance (feedback_events.edit_distance)."""
    ta, tb = tokenize(a), tokenize(b)
    if len(ta) < len(tb):
        ta, tb = tb, ta
    if not tb:
        return len(ta)
    prev = list(range(len(tb) + 1))
    for i, wa in enumerate(ta, start=1):
        cur = [i]
        for j, wb in enumerate(tb, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wa != wb)))
        prev = cur
    return prev[-1]


def clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def rescale(x: float, a: float, b: float) -> float:
    if b <= a:
        return clip01(x)
    return clip01((x - a) / (b - a))
