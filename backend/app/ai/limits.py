"""Platform hard limits and length presets (Section 3.2 / Section 4 step 9)."""
from __future__ import annotations

PLATFORM_LIMITS: dict[str, int] = {
    "instagram": 2200,
    "x": 280,
    "twitter": 280,
    "linkedin": 3000,
    "facebook": 5000,
    "whatsapp": 65536,
    "email": 20000,
    "blog": 50000,
}

# word ranges per preset; "medium" is the Section 17 worked example (40-60 words)
LENGTH_PRESETS: dict[str, tuple[int, int]] = {
    "short": (15, 30),
    "medium": (40, 60),
    "long": (70, 100),
}

LENGTH_TOLERANCE = 0.15  # +/- 15% around the requested range


def preset_range(preset: str) -> tuple[int, int]:
    return LENGTH_PRESETS.get((preset or "medium").lower(), LENGTH_PRESETS["medium"])


def platform_limit(platform: str) -> int:
    return PLATFORM_LIMITS.get((platform or "instagram").lower(), PLATFORM_LIMITS["instagram"])


def acceptable_range(preset: str) -> tuple[int, int]:
    lo, hi = preset_range(preset)
    return int(lo * (1 - LENGTH_TOLERANCE)), int(hi * (1 + LENGTH_TOLERANCE))
