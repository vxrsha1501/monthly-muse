"""MonthlyMuse AI/NLP package.

Pure Python + NumPy: preprocessing, embeddings, cosine similarity, matrices,
features, scoring, MMR, Thompson sampling, Monte-Carlo confidence, tone
classification, personalization, template fallback and the LLM adapters.
No database or HTTP framework imports (Section 10.1 rule) - except the adapter
module, which owns the single outbound provider dependency.
"""
from app.ai.arms import TONES, PURPOSES, STRUCTURES  # noqa: F401
from app.ai.pipeline import (  # noqa: F401
    Brief, Candidate, HistoryItem, PersonalizationState, Pipeline, PipelineResult,
)
