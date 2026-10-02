"""Generate data/tone_training.csv - labelled short messages for the tone classifier.

Deterministic: run `python scripts/generate_tone_data.py` to regenerate the CSV.
8 tones x 100 messages = 800 rows, matching the 600-1000 range in Section 5.1.1.
"""
from __future__ import annotations

import csv
import os
import random

TONES = ("warm", "playful", "professional", "inspirational", "witty", "grateful", "urgent", "formal")

OPENERS = {
    "warm": ["We've been thinking about you", "A little note from our corner", "Hello from all of us",
             "Just a warm hello", "We wanted to reach out"],
    "playful": ["Plot twist", "Well, this is fun", "Tiny announcement, big energy", "Guess what",
                "Okay, hear us out"],
    "professional": ["We are writing to share", "This month we are introducing", "Please note the following update",
                     "We would like to inform you", "As part of our ongoing commitment"],
    "inspirational": ["Every season brings a new beginning", "Small steps add up", "Growth starts with one choice",
                      "There is always room to begin again", "The best moments start quietly"],
    "witty": ["We checked the calendar twice", "Against our better judgement", "In a shocking turn of events",
              "The committee has spoken", "Breaking news nobody asked for"],
    "grateful": ["Thank you", "With genuine appreciation", "We are so grateful", "A heartfelt thank you",
                 "You made our month"],
    "urgent": ["Last chance", "Do not wait", "Time is running out", "Act today", "Final call"],
    "formal": ["Dear friends and colleagues", "Please accept this notice", "We hereby announce",
               "To our valued community", "We take this opportunity to inform you"],
}

MIDDLES = {
    "warm": ["we saved a quiet spot for you this month", "everything is ready when you are",
             "the team asked me to say hello", "we would love to see your face"],
    "playful": ["our calendar did a happy dance", "the countdown has started and the snacks are ready",
                "we practised this announcement in the mirror", "the cat approved, so it is official"],
    "professional": ["the updated schedule is now available", "the initiative will begin at the start of the month",
                     "participation is open to all registered members", "further details are attached below"],
    "inspirational": ["the smallest habit this week can shape the whole month",
                      "you are closer than you think to the change you want",
                      "progress is built from the moments nobody sees", "start where you are, with what you have"],
    "witty": ["we would explain the maths but the printer is tired", "yes, another update, and yes, it is worth it",
              "our marketing intern approved this message", "allegedly the best decision you will make this week"],
    "grateful": ["your support carried us through the whole season", "every message from you makes a difference",
                 "we notice every single one of you", "because of you this month actually mattered"],
    "urgent": ["the offer closes before the month does", "only a handful of places remain",
               "this window shuts sooner than you expect", "the clock is genuinely ticking"],
    "formal": ["the arrangements have been made in accordance with the schedule",
               "kindly confirm your participation at your earliest convenience",
               "the enclosed information supersedes all previous notices", "all relevant details are set out below"],
}

CLOSERS = {
    "warm": ["Come say hello.", "We will keep the kettle on.", "See you soon, friend."],
    "playful": ["High five if you read this far.", "Tag a friend who needs this.", "Snacks await."],
    "professional": ["Please do not hesitate to contact us.", "We look forward to your response.",
                     "Further information is available on request."],
    "inspirational": ["Here is to the month ahead.", "You have got this.", "Begin today."],
    "witty": ["You are welcome.", "No refunds on excitement.", "Details, allegedly, below."],
    "grateful": ["Thank you, truly.", "With thanks from all of us.", "We appreciate you."],
    "urgent": ["Do not miss out.", "Secure your place now.", "Move quickly."],
    "formal": ["Yours faithfully.", "We remain at your disposal.", "Please acknowledge receipt."],
}

TOPICS = ("our monthly offer", "the new season menu", "a community meetup", "loyalty rewards",
          "a workshop this month", "weekend openings", "a fresh batch of news", "member perks")


def build(rng: random.Random) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for tone in TONES:
        for _ in range(100):
            parts = [
                rng.choice(OPENERS[tone]),
                rng.choice(MIDDLES[tone]),
                f"about {rng.choice(TOPICS)}",
                rng.choice(CLOSERS[tone]),
            ]
            if rng.random() < 0.35:
                parts.insert(1, rng.choice(MIDDLES[tone]))
            text = ", ".join(parts[:2]) + ". " + ". ".join(parts[2:]) + "."
            rows.append((text, tone))
    return rows


def main() -> None:
    rng = random.Random(42)
    rows = build(rng)
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tone_training.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["text", "tone"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
