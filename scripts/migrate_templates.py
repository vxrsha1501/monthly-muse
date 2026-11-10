"""One-off migration: audit Task 1/2 fixes for data/templates.json.

1. Add 24 occasion-free templates (t41-t64) so generation works when the
   occasion gates drop an irrelevant/past holiday.
2. Tag future-phrased occasion templates with "future_only" so a past occasion
   never renders "arrives this month" phrasing.
3. Trim keyword_ref variants to the plain noun - appositional variants broke
   grammar in NP slots ("Gandhi Jayanti is better with team - just for you").
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "templates.json")

NEW = [
    # id, purpose, tone, structure, text
    ("t41", "inform", "warm", "story-opening",
     "Good things are blooming this {month}. We put together {keyword_ref} for {audience_ref} "
     "because you asked - simple, useful and ready when you are. Here's everything in one place. {cta}"),
    ("t42", "inform", "playful", "punchy",
     "Straight to it, {audience_ref}: {keyword_ref} is live and it actually works. "
     "No fine print, no fuss - just the good stuff, all {month}. {cta}"),
    ("t43", "inform", "professional", "announcement",
     "Update for {audience_ref}: this {month} we are introducing {keyword_ref}, prepared with care "
     "and available without delay. The essentials are below. {cta}"),
    ("t44", "inform", "witty", "question-hook",
     "What's the one thing {audience_ref} keep asking about? {keyword_ref} - that's what. "
     "It's here, it's simple, and it's ours to share this {month}. {cta}"),
    ("t45", "inform", "inspirational", "story-opening",
     "Every {season} brings a fresh chance to start well. This {month}, {keyword_ref} is our small "
     "way of helping {audience_ref} take the next step. {cta}"),
    ("t46", "inform", "urgent", "announcement",
     "Quick one for {audience_ref}: {keyword_ref} is ready now and worth a look while the {season} "
     "lasts. Don't put it off. {cta}"),
    ("t47", "inform", "formal", "personal-note",
     "Dear {audience_ref}, we are pleased to share details of {keyword_ref} for this {month}. "
     "The information is straightforward and applies throughout the season. {cta}"),
    ("t48", "inform", "grateful", "gratitude-first",
     "Thank you, {audience_ref} - you keep this going. This {month} we are sharing {keyword_ref} "
     "as a small token of that appreciation, whenever you are ready. {cta}"),
    ("t49", "promote", "warm", "story-opening",
     "There is a reason {audience_ref} keep coming back: {keyword_ref}, done properly, all {month}. "
     "Pull up a chair and see what we mean. {cta}"),
    ("t50", "promote", "playful", "punchy",
     "New on the menu: {keyword_ref}. It's the {season} pick {audience_ref} didn't know they needed "
     "- and yes, it's as good as it sounds. {cta}"),
    ("t51", "promote", "professional", "announcement",
     "Announcing {keyword_ref} for {audience_ref}, available this {month}. Straightforward terms, "
     "limited availability, no surprises. Details are below. {cta}"),
    ("t52", "promote", "urgent", "punchy",
     "Heads up {audience_ref}: {keyword_ref} won't stay around all {month}. If you've been meaning "
     "to, now is the moment. {cta}"),
    ("t53", "announce", "warm", "announcement",
     "Big news, {audience_ref}: {keyword_ref} is here. We've been working on it since the {season} "
     "and we can't wait for you to see it. {cta}"),
    ("t54", "announce", "playful", "punchy",
     "Well, this is fun: {keyword_ref} is officially a thing now. {audience_ref} get first look, "
     "because you're the reason it exists. {cta}"),
    ("t55", "announce", "professional", "announcement",
     "We are pleased to announce {keyword_ref} for {audience_ref}. It goes live this {month}; "
     "everything you need to know is below. {cta}"),
    ("t56", "announce", "inspirational", "story-opening",
     "It started with a simple idea this {season}: {keyword_ref}, made for {audience_ref}. "
     "Today it's real, and it's ready for you. {cta}"),
    ("t57", "announce", "witty", "question-hook",
     "Guess what just arrived? {keyword_ref}. {audience_ref} have been asking - here it is, "
     "in {month} form. {cta}"),
    ("t58", "engage", "warm", "question-hook",
     "Quick question for {audience_ref}: what would make this {month} better? We're building around "
     "{keyword_ref} and we'd love your take. {cta}"),
    ("t59", "engage", "playful", "question-hook",
     "Hot take territory: {keyword_ref} is underrated. Disagree? {audience_ref}, tell us below - "
     "we read every reply. {cta}"),
    ("t60", "engage", "professional", "personal-note",
     "We would like to hear from {audience_ref} this {month}. What do you make of {keyword_ref}? "
     "Your feedback shapes what comes next. {cta}"),
    ("t61", "thank", "grateful", "gratitude-first",
     "Gratitude first: {audience_ref}, thank you for a wonderful {season}. {keyword_ref} is our way "
     "of saying we noticed, this {month} and beyond. {cta}"),
    ("t62", "thank", "warm", "personal-note",
     "Thank you, {audience_ref} - truly. Because you showed up all {season}, we're marking it with "
     "{keyword_ref} this {month}. {cta}"),
    ("t63", "celebrate", "warm", "story-opening",
     "Every {month} gives us something worth marking. This one, {audience_ref}, is about "
     "{keyword_ref} - and a small celebration is overdue. {cta}"),
    ("t64", "celebrate", "playful", "punchy",
     "Cue the confetti: {keyword_ref} deserves a moment, and so do you, {audience_ref}. "
     "Celebrating all {month}. {cta}"),
]

# Templates whose phrasing only works for an upcoming occasion.
FUTURE_ONLY = {"t06", "t10", "t11", "t13", "t15", "t16", "t18", "t20", "t24",
               "t27", "t29", "t30", "t32", "t34", "t37", "t39", "t40",
               # past-occasion sweep: templates that assert the occasion is
               # being marked/celebrated *this month* - wrong phrasing when the
               # occasion already passed ("This November, we're marking
               # Gandhi Jayanti", "To celebrate {occasion} ... this {month}")
               "t01", "t03", "t07", "t14", "t17", "t21", "t22", "t25",
               "t26", "t33", "t35", "t36"}


def main() -> None:
    with open(PATH, encoding="utf-8") as fh:
        data = json.load(fh)

    existing = {t["id"] for t in data["templates"]}
    added = 0
    for tid, purpose, tone, structure, text in NEW:
        if tid in existing:
            continue
        data["templates"].append({"id": tid, "purpose": purpose, "tone": tone,
                                  "structure": structure, "text": text})
        added += 1

    tagged = 0
    for t in data["templates"]:
        if t["id"] in FUTURE_ONLY:
            t["future_only"] = True
            tagged += 1

    # appositional variants broke grammar in noun-phrase slots (Task 2)
    data["slots"]["keyword_ref"] = ["{keyword}"]

    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    occ = sum(1 for t in data["templates"] if "{occasion" in t["text"])
    free = len(data["templates"]) - occ
    print(f"templates={len(data['templates'])} added={added} future_only={tagged} "
          f"occasion={occ} occasion_free={free}")
    assert added == len(NEW) - (len(existing & {n[0] for n in NEW})), "unexpected add count"
    assert free >= 20, "need a healthy occasion-free pool"


if __name__ == "__main__":
    sys.exit(main())
