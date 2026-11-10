"""Occasion relevance and deterministic date eligibility (audit Task 1, review P1/P2).

The product rule this module encodes:

    "an occasion exists in the calendar"   -X->  mention the occasion
    "the occasion relates to the objective" -->  mention the occasion

Two independent gates decide whether an occasion may enter generation context:

1. **Relevance** - does the post objective (topic + keywords + instructions)
   semantically relate to the occasion?  Measured as the coverage of the
   occasion's *name* tokens by the objective's content tokens, plus a narrow
   category bridge that lets a genuinely festive objective ("holiday sale",
   "festive menu") pair with any holiday/season-category occasion.  Hiring,
   recruitment, informational or other unrelated objectives carry no festive
   vocabulary and therefore never trip the bridge.

2. **Date eligibility** - has the occasion already passed?  Deterministic,
   clock-explicit: the caller always supplies ``reference`` (today in the
   user's timezone) and optionally ``post_date`` (the cycle's posting date).
   A past occasion never enters context *automatically*; an explicitly chosen
   past occasion is treated as a deliberate retrospective (still gated by
   relevance).

Everything here is pure and deterministic - no DB access, no hidden clock - so
the decision is unit-testable and can be persisted in the request trace.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date

from app.ai.text import content_tokens

# An occasion is relevant when >= this fraction of its name tokens appear in
# the objective, or when the festive-intent bridge fires (which scores 0.5).
RELEVANCE_THRESHOLD = 0.5
_BRIDGE_SCORE = 0.5

# Objective vocabulary that signals the post is *about* a celebration / holiday
# period.  Lemmas only (content_tokens lemmatises), matched case-insensitively.
# Deliberately excludes generic commercial words (offer, sale, promotion, deal)
# so "job offer" or "sales hiring" can never bridge to an unrelated holiday.
FESTIVE_INTENT = frozenset({
    "holiday", "festive", "festival", "celebration", "celebrate", "festivity",
    "festivities", "seasonal", "season", "gifting",
})

# Occasion categories that carry festive meaning (OccasionCategory values).
FESTIVE_CATEGORIES = frozenset({"holiday", "season"})


@dataclass
class OccasionDecision:
    """Why an occasion was used or dropped - persisted in the request trace."""

    used: bool = False
    name: str = ""
    source: str = "none"           # user | auto | none
    relevance: float = 0.0
    relevance_reason: str = ""
    date_status: str = "unknown"   # past | today | upcoming | unknown
    date_iso: str | None = None
    reason: str = ""               # human-readable why used / why dropped
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "used": self.used, "name": self.name, "source": self.source,
            "relevance": round(self.relevance, 3),
            "relevance_reason": self.relevance_reason,
            "date_status": self.date_status, "date": self.date_iso,
            "reason": self.reason, "warnings": list(self.warnings),
        }


# --------------------------------------------------------------- dates
def occurrence_date(month: int, day: int | None, year: int) -> date | None:
    """The occasion's calendar date inside the target month's year."""
    if day is None:
        return None
    return date(year, month, day)


def nearest_occurrence(occ_month: int, day: int | None, target_month: int,
                       target_year: int) -> date:
    """The occasion's real calendar occurrence nearest to the target month.

    The occasion's date comes from its OWN month (``occ_month``), never from
    the target month: Gandhi Jayanti is Oct 2, whatever month the post is for.
    Of the occurrence in target_year - 1 / target_year / target_year + 1, the
    one closest to mid-target-month wins, so a December holiday picked for a
    March post resolves to the December *before* it (the one the post would
    actually be referring to), not to next December.  Day is clamped to short
    months (day 31 in February -> Feb 28/29).  Undated occasions resolve to
    mid-month of their month (month-level semantics only).
    """
    anchor = date(target_year, target_month, 15)
    best: date | None = None
    for y in (target_year - 1, target_year, target_year + 1):
        last = monthrange(y, occ_month)[1]
        d = date(y, occ_month, min(day, last) if day else 15)
        if best is None or abs((d - anchor).days) < abs((best - anchor).days):
            best = d
    assert best is not None
    return best


def date_status(month: int, day: int | None, year: int, reference: date) -> str:
    """past | today | upcoming | unknown, relative to ``reference`` (explicit clock).

    ``year`` is the year of the *target month* being generated for, so a post
    generated in October for a January target month compares against next
    January, not this one.
    """
    d = occurrence_date(month, day, year)
    if d is None:
        # Undated occasion (e.g. lunar dates without a fixed day): only the
        # month matters - past if the whole target month is behind us.
        ref_ym = (reference.year, reference.month)
        tgt_ym = (year, month)
        return "past" if tgt_ym < ref_ym else "upcoming"
    if d < reference:
        return "past"
    if d == reference:
        return "today"
    return "upcoming"


# --------------------------------------------------------------- relevance
def relevance_score(objective: str, occasion_name: str, category: str | None = None
                    ) -> tuple[float, str]:
    """How much the post objective actually relates to the occasion, in [0, 1].

    Returns (score, reason).  Two mechanisms, deterministic and general:

    * name coverage - the fraction of the occasion's name content-tokens that
      appear in the objective ("20% Gandhi Jayanti special offer" covers
      "gandhi jayanti" fully; "part-time barista hiring" covers none).
    * festive bridge - a festive objective (holiday / festive / celebration
      vocabulary) may pair with a holiday- or season-category occasion.
    """
    obj = set(content_tokens(objective or ""))
    name = set(content_tokens(occasion_name or ""))
    if not name:
        return 0.0, "no_occasion_name"
    covered = name & obj
    coverage = len(covered) / len(name)
    if coverage >= RELEVANCE_THRESHOLD:
        return min(1.0, coverage), f"objective mentions the occasion ({', '.join(sorted(covered))})"

    festive_obj = obj & FESTIVE_INTENT
    if festive_obj and (category or "").lower() in FESTIVE_CATEGORIES:
        return _BRIDGE_SCORE, (f"festive objective ({', '.join(sorted(festive_obj))}) "
                               f"matches {category} occasion")
    return round(coverage, 3), "objective does not mention the occasion"


# --------------------------------------------------------------- decision
def decide_occasion(*, name: str, month: int, year: int, objective: str,
                    category: str | None, description: str = "",
                    day: int | None = None, reference: date,
                    post_date: date | None = None, explicit: bool,
                    occ_month: int | None = None) -> OccasionDecision:
    """Single entry point: relevance gate + date gate -> use or drop.

    ``explicit=True`` means the user picked this occasion as a content driver;
    it still must be relevant, but a *past* explicit occasion is allowed as a
    deliberate retrospective (with a warning).  ``explicit=False`` is the
    automatic month-suggestion path, which requires both relevance and date
    eligibility.

    ``occ_month`` is the occasion's real calendar month (from the Occasion
    row).  It defaults to the target month because automatic suggestions only
    ever come from the target month; an explicit pick may name an occasion
    from any month, and its date gate must use the real date - Gandhi Jayanti
    (Oct 2) is past on Oct 8 even for a November post.
    """
    if not name:
        return OccasionDecision(used=False, source="user" if explicit else "auto",
                                reason="no occasion selected")

    occ_m = occ_month or month
    if day is None:
        # undated observance: month-level semantics only - past only once the
        # whole occurrence month is behind us (day is unknown).
        d = nearest_occurrence(occ_m, None, month, year)
        status = ("past" if (d.year, d.month) < (reference.year, reference.month)
                  else "upcoming")
    else:
        d = nearest_occurrence(occ_m, day, month, year)
        status = "past" if d < reference else ("today" if d == reference else "upcoming")
    score, rel_reason = relevance_score(objective, name, category)
    decision = OccasionDecision(
        used=False, name=name, source="user" if explicit else "auto",
        relevance=score, relevance_reason=rel_reason, date_status=status,
        date_iso=(d.isoformat() if (d and day is not None) else None),
    )

    # Gate 1 - relevance applies to EVERY path, including explicit picks:
    # a hiring post must not be steered by an unselected-topic holiday.
    if score < RELEVANCE_THRESHOLD:
        decision.reason = (f"not relevant to the post objective "
                           f"(relevance {score:.2f} < {RELEVANCE_THRESHOLD:.2f})")
        if explicit:
            decision.warnings.append(
                f"Ignored occasion '{name}': it does not relate to your post's topic. "
                f"Mention it in the topic if you really want it included.")
        return decision

    # Gate 2 - date eligibility.  Automatic occasions must be in the future
    # both now and at posting time; explicit ones may be a retrospective.
    if not explicit:
        if status == "past":
            decision.reason = "occasion has already passed"
            return decision
        # undated observances have no concrete day to compare with posting
        if post_date is not None and day is not None and d < post_date:
            decision.reason = f"occasion date {d.isoformat()} is before the posting date {post_date.isoformat()}"
            return decision
    else:
        if status == "past":
            decision.warnings.append(
                f"Occasion '{name}' has already passed - it will be framed as a "
                f"past/retrospective mention, never as upcoming.")

    decision.used = True
    decision.reason = ("explicitly selected and relevant" if explicit
                       else "relevant and date-eligible")
    return decision


# --------------------------------------------------------------- suggestions
def sort_suggestions(rows, month: int, year: int, reference: date):
    """Date-aware ordering for the form's suggestion chips: upcoming first
    (nearest date first), past occasions last - still visible, never hidden."""
    def key(r):
        status = date_status(month, getattr(r, "day", None), year, reference)
        upcoming = 0 if status in ("upcoming", "today", "unknown") else 1
        return (upcoming, getattr(r, "day", None) or 99)
    return sorted(rows, key=key)


__all__ = ["OccasionDecision", "RELEVANCE_THRESHOLD", "FESTIVE_INTENT",
           "FESTIVE_CATEGORIES", "occurrence_date", "nearest_occurrence",
           "date_status", "relevance_score", "decide_occasion", "sort_suggestions"]
