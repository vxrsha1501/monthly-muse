"""Process-wide runtime: models are loaded once at start-up (Section 10.2)."""
from __future__ import annotations

import logging
import os
import threading

from app.ai.embedder import get_embedder
from app.ai.llm_adapters import GenerationService
from app.ai.pipeline import Pipeline
from app.ai.tone import load_or_train
from app.core.config import settings

logger = logging.getLogger("monthlymuse.runtime")


class Runtime:
    _lock = threading.Lock()
    _initialised = False

    embedder = None
    tone_classifier = None
    pipeline: Pipeline | None = None
    generator: GenerationService | None = None

    @classmethod
    def init(cls, force: bool = False) -> "Runtime":
        with cls._lock:
            if cls._initialised and not force:
                return cls
            cls.embedder = get_embedder(settings.embedder, settings.embedding_dim, settings.embedding_model)
            tone_csv = os.path.join(settings.data_dir, "tone_training.csv")
            cls.tone_classifier = load_or_train(tone_csv, cls.embedder)
            cls.generator = GenerationService()
            cls.pipeline = Pipeline(embedder=cls.embedder, tone_classifier=cls.tone_classifier,
                                    generator=cls.generator, seed=settings.random_seed)
            cls._initialised = True
            logger.info("runtime_ready embedder=%s tone_labels=%d provider=%s",
                        cls.embedder.name, len(cls.tone_classifier.labels), settings.llm_provider)
        return cls

    @classmethod
    def ready(cls) -> bool:
        return cls._initialised


def get_runtime() -> Runtime:
    return Runtime.init()
