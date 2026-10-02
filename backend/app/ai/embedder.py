"""Sentence embeddings behind one interface.

Default: deterministic feature-hashing embedder (384-d, local, free) - the
"lighter hashing-based fallback for local dev" from Section 20. If
sentence-transformers is installed (optional extra), set MM_EMBEDDER=sentence-transformers
to use all-MiniLM-L6-v2 as the blueprint specifies. All vectors are L2-normalised
so cosine similarity is a plain dot product.
"""
from __future__ import annotations

import hashlib
import logging
import re

import numpy as np

logger = logging.getLogger("monthlymuse.ai")

_TOKEN_RE = re.compile(r"[a-zA-Z0-9']+")

# content words dominate; stopwords get a small weight so they barely move direction
_STOP = frozenset("""
a an the and or but if then so as of at by for with to from in on is are was were be
it its this that i you he she we they them our your my me us not no all very can will
""".split())


def _hash_token(token: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    idx = int.from_bytes(digest[:4], "little") % dim
    sign = 1.0 if digest[4] & 1 else -1.0
    return idx, sign


class HashingEmbedder:
    """Feature hashing over unigrams + bigrams with signed weights, then L2 norm."""

    name = "hashing-384"

    def __init__(self, dim: int = 384, seed: int = 42) -> None:
        self.dim = dim
        self.seed = seed

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = [t.lower() for t in _TOKEN_RE.findall(text or "") if t.strip()]
            vec = out[row]
            for i, tok in enumerate(tokens):
                weight = 0.35 if tok in _STOP else 1.0
                idx, sign = _hash_token(tok, self.dim)
                vec[idx] += sign * weight
                if i + 1 < len(tokens):
                    bi = f"{tok}__{tokens[i + 1]}"
                    idx2, sign2 = _hash_token(bi, self.dim)
                    vec[idx2] += sign2 * 1.2
        # L2 normalise (zero vectors stay zero)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


class SentenceTransformerEmbedder:
    """Optional, blueprint-exact embedder (all-MiniLM-L6-v2, 384-d). Loaded lazily."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # optional dep

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.name = model_name

    def embed(self, texts: list[str]) -> np.ndarray:
        arr = self._model.encode(list(texts), normalize_embeddings=True)
        return np.asarray(arr, dtype=np.float32)

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


_embedder_cache: dict[str, object] = {}


def get_embedder(kind: str = "hash", dim: int = 384, model_name: str = "all-MiniLM-L6-v2"):
    key = f"{kind}:{dim}:{model_name}"
    if key not in _embedder_cache:
        if kind == "sentence-transformers":
            try:
                _embedder_cache[key] = SentenceTransformerEmbedder(model_name)
                logger.info("embedder_loaded", extra={"stage": model_name})
            except Exception as exc:  # pragma: no cover - optional dependency
                logger.warning("embedder_fallback: %s", exc)
                _embedder_cache[key] = HashingEmbedder(dim)
        else:
            _embedder_cache[key] = HashingEmbedder(dim)
    return _embedder_cache[key]
