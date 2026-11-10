"""Regression tests: occasion relevance + deterministic date gating.

Audit Task 1 / review P1-P2:
  * an occasion on the calendar must not be forced into an unrelated post
  * an objective that mentions the occasion keeps it
  * past occasions never enter automatic context; explicit past = retrospective
All month arithmetic is relative to ``date.today()`` so the tests never go stale.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.ai.occasion import (date_status, decide_occasion, nearest_occurrence,
                             occurrence_date, relevance_score, sort_suggestions)
from app.db.models import User
from app.db.session import SessionLocal
from app.schemas.generation import GenerateIn
from app.services.generation_service import build_brief


def _month_offset(offset: int) -> tuple[int, int]:
    """(year, month) of today + offset months - clock-relative, never stale."""
    today = date.today()
    idx = today.year * 12 + (today.month - 1) + offset
    year, month = divmod(idx, 12)
    return year, month + 1


# ------------------------------------------------------------ relevance unit
def test_hiring_objective_is_irrelevant_to_holiday():
    score, reason = relevance_score("Part-time barista hiring", "Gandhi Jayanti", "holiday")
    assert score < 0.5, reason
    score, _ = relevance_score("Part-time job hiring", "Gandhi Jayanti", "holiday")
    assert score < 0.5


def test_objective_mentioning_the_occasion_is_relevant():
    assert relevance_score("20% Gandhi Jayanti special offer", "Gandhi Jayanti", "holiday")[0] >= 0.5
    assert relevance_score("Diwali special menu", "Diwali", "holiday")[0] >= 0.5
    assert relevance_score("Christmas sale on gift hampers", "Christmas season", "holiday")[0] >= 0.5


def test_festive_objective_bridges_to_holiday_but_commercial_words_do_not():
    # a genuinely festive objective may pair with a holiday-category occasion
    assert relevance_score("holiday promotion", "Gandhi Jayanti", "holiday")[0] >= 0.5
    assert relevance_score("festive season menu", "Diwali", "holiday")[0] >= 0.5
    # generic commercial / recruiting words must never bridge
    assert relevance_score("job offer hiring drive", "Gandhi Jayanti", "holiday")[0] < 0.5
    assert relevance_score("sales promotion for staff", "Gandhi Jayanti", "holiday")[0] < 0.5


def test_awareness_category_never_bridges():
    assert relevance_score("holiday promotion", "World Health Day", "awareness")[0] < 0.5


# ------------------------------------------------------------ date unit
def test_date_status_boundaries_are_deterministic():
    ref = date(2026, 10, 8)
    assert date_status(10, 7, 2026, ref) == "past"
    assert date_status(10, 8, 2026, ref) == "today"
    assert date_status(10, 9, 2026, ref) == "upcoming"
    assert date_status(11, None, 2026, ref) == "upcoming"   # undated, future month
    assert date_status(9, None, 2026, ref) == "past"        # undated, past month


def test_occurrence_uses_target_year():
    assert occurrence_date(1, 1, 2027) == date(2027, 1, 1)
    assert occurrence_date(12, 31, 2026) == date(2026, 12, 31)
    assert occurrence_date(11, None, 2026) is None


def test_nearest_occurrence_uses_the_occasions_real_month_not_the_target_month():
    # Gandhi Jayanti = Oct 2.  For a November post on Oct 8 2026 the real
    # date is Oct 2 2026 - PAST - never "Nov 2, upcoming".
    d = nearest_occurrence(10, 2, target_month=11, target_year=2026)
    assert d == date(2026, 10, 2)
    # Christmas picked for a March post resolves to the December BEFORE it
    d = nearest_occurrence(12, 25, target_month=3, target_year=2027)
    assert d == date(2026, 12, 25)
    # day clamped to short months (31 Feb)
    d = nearest_occurrence(2, 31, target_month=2, target_year=2027)
    assert d == date(2027, 2, 28)
    d = nearest_occurrence(2, 31, target_month=2, target_year=2028)
    assert d == date(2028, 2, 29)                  # leap year
    # undated observance -> mid-month representative of its own month
    d = nearest_occurrence(9, None, target_month=11, target_year=2026)
    assert (d.year, d.month) == (2026, 9)


def test_out_of_month_past_occasion_is_past_even_for_a_future_target_month():
    d = decide_occasion(name="Gandhi Jayanti", month=11, year=2026, occ_month=10,
                        objective="Thank you for Gandhi Jayanti support",
                        category="holiday", day=2, reference=date(2026, 10, 8),
                        explicit=True)
    assert d.used and d.date_status == "past"
    assert d.date_iso == "2026-10-02"
    assert d.warnings and "already passed" in d.warnings[0]


def test_out_of_month_upcoming_occasion_resolves_to_the_nearest_occurrence():
    d = decide_occasion(name="Christmas", month=3, year=2027, occ_month=12,
                        objective="Christmas gift hampers", category="holiday",
                        day=25, reference=date(2026, 10, 8), explicit=True)
    assert d.used and d.date_status == "upcoming"
    assert d.date_iso == "2026-12-25"


# ------------------------------------------------------------ decision unit
def test_auto_past_occasion_is_dropped():
    year, month = _month_offset(-2)
    d = decide_occasion(name="Some Holiday", month=month, year=year,
                        objective="Some Holiday special menu", category="holiday",
                        day=15, reference=date.today(), explicit=False)
    assert not d.used and d.date_status == "past" and "passed" in d.reason


def test_auto_upcoming_relevant_occasion_is_kept():
    year, month = _month_offset(2)
    d = decide_occasion(name="Some Holiday", month=month, year=year,
                        objective="Some Holiday special menu", category="holiday",
                        day=15, reference=date.today(), explicit=False)
    assert d.used and d.date_status == "upcoming"


def test_auto_irrelevant_occasion_is_dropped_even_if_upcoming():
    year, month = _month_offset(2)
    d = decide_occasion(name="Some Holiday", month=month, year=year,
                        objective="Part-time barista hiring", category="holiday",
                        day=15, reference=date.today(), explicit=False)
    assert not d.used and "not relevant" in d.reason


def test_explicit_past_occasion_is_allowed_with_warning():
    year, month = _month_offset(-2)
    d = decide_occasion(name="Some Holiday", month=month, year=year,
                        objective="Thank you for our Some Holiday drive",
                        category="holiday", day=15, reference=date.today(), explicit=True)
    assert d.used and d.date_status == "past"
    assert d.warnings and "already passed" in d.warnings[0]


def test_explicit_irrelevant_occasion_is_still_dropped_with_warning():
    year, month = _month_offset(2)
    d = decide_occasion(name="Some Holiday", month=month, year=year,
                        objective="Part-time barista hiring", category="holiday",
                        day=15, reference=date.today(), explicit=True)
    assert not d.used
    assert d.warnings and "does not relate" in d.warnings[0]


def test_occasion_before_the_posting_date_is_dropped_automatically():
    year, month = _month_offset(1)
    d = decide_occasion(name="Some Holiday", month=month, year=year,
                        objective="Some Holiday special menu", category="holiday",
                        day=5, reference=date.today(), post_date=date(year, month, 20),
                        explicit=False)
    assert not d.used and "before the posting date" in d.reason


def test_suggestions_sort_upcoming_first():
    year, month = _month_offset(0)
    class Row:
        def __init__(self, name, day):
            self.name, self.day = name, day
    rows = [Row("late", 28), Row("early-past", 1), Row("mid", 15)]
    # reference = end of the target month => day 1 is past, 15/28 upcoming... use +1 day ref
    ref = date(year, month, 2) if month else date(year, month, 2)
    ordered = sort_suggestions(rows, month, year, ref)
    assert ordered[0].name == "mid"               # nearest upcoming first
    assert ordered[-1].name == "early-past"       # past occasions still visible, but last
    assert [r.name for r in ordered] == ["mid", "late", "early-past"]


# ------------------------------------------------------------ service level
def _build(client, auth_headers, payload: dict):
    me = client.get("/api/v1/me", headers=auth_headers).json()
    db = SessionLocal()
    try:
        user = db.get(User, me["id"])
        return build_brief(db, user, GenerateIn(**payload))
    finally:
        db.close()


def _first_occasion(client, auth_headers, month: int) -> dict:
    rows = client.get("/api/v1/occasions", headers=auth_headers,
                      params={"month": month}).json()
    assert rows, f"seed data must include month {month}"
    return rows[0]


def test_service_auto_hiring_post_gets_no_occasion(client, auth_headers):
    year, month = _month_offset(2)
    brief = _build(client, auth_headers, {
        "month": f"{year:04d}-{month:02d}", "topic": "Part-time barista hiring",
        "keywords": ["hiring", "part-time"], "purpose": "announce",
        "tones": ["warm"], "platform": "instagram",
    })
    assert brief.occasion == ""
    assert brief.occasion_decision["used"] is False
    assert "not relevant" in brief.occasion_decision["reason"]


def test_service_relevant_occasion_survives_for_future_month(client, auth_headers):
    year, month = _month_offset(2)
    occ = _first_occasion(client, auth_headers, month)
    topic = f"{occ['name']} special offer"          # objective names the occasion
    brief = _build(client, auth_headers, {
        "month": f"{year:04d}-{month:02d}", "topic": topic,
        "keywords": ["special", "offer"], "purpose": "promote",
        "tones": ["warm"], "platform": "instagram",
    })
    assert brief.occasion == occ["name"]
    assert brief.occasion_decision["used"] is True
    assert brief.occasion_status in ("upcoming", "today", "unknown")


def test_service_past_month_auto_occasion_never_enters_context(client, auth_headers):
    year, month = _month_offset(-2)
    occ = _first_occasion(client, auth_headers, month)
    brief = _build(client, auth_headers, {
        "month": f"{year:04d}-{month:02d}", "topic": f"{occ['name']} special offer",
        "keywords": ["special"], "purpose": "promote",
        "tones": ["warm"], "platform": "instagram",
    })
    assert brief.occasion == ""
    assert brief.occasion_decision["date_status"] == "past"


def test_service_explicit_none_choice_is_honoured(client, auth_headers):
    year, month = _month_offset(2)
    brief = _build(client, auth_headers, {
        "month": f"{year:04d}-{month:02d}", "topic": "New menu launch",
        "occasion": "", "tones": ["warm"], "platform": "instagram",
    })
    assert brief.occasion == ""
    assert brief.occasion_decision["source"] == "user"


def test_service_irrelevant_explicit_occasion_warns(client, auth_headers):
    year, month = _month_offset(2)
    occ = _first_occasion(client, auth_headers, month)
    brief = _build(client, auth_headers, {
        "month": f"{year:04d}-{month:02d}", "topic": "Part-time barista hiring",
        "occasion": occ["name"], "keywords": ["hiring"],
        "tones": ["warm"], "platform": "instagram",
    })
    assert brief.occasion == ""
    assert brief.warnings and occ["name"] in brief.warnings[0]


def test_service_explicit_occasion_from_an_earlier_month_is_marked_past(client, auth_headers):
    """An occasion from last month, picked for next month's post: its real
    date has passed, so the decision must say 'past' (clock-relative test)."""
    occ_month_year, occ_month = _month_offset(-1)
    occ = _first_occasion(client, auth_headers, occ_month)
    year, month = _month_offset(1)
    brief = _build(client, auth_headers, {
        "month": f"{year:04d}-{month:02d}",
        "topic": f"{occ['name']} thank-you post",
        "occasion": occ["name"], "keywords": [occ["name"].split()[0].lower()],
        "tones": ["warm"], "platform": "instagram",
    })
    # relevant objective keeps it, but the date gate must call it PAST
    assert brief.occasion == occ["name"]
    assert brief.occasion_decision["used"] is True
    assert brief.occasion_status == "past", brief.occasion_decision
    assert any("already passed" in w for w in brief.warnings)
