"""Exa web search for retrieval (https://exa.ai/docs).

    search_web(query, num_results=5) -> [{"title", "url", "highlights"}]

The API key is read from the EXA_API_KEY environment variable (declared empty in
.env.example); nothing is hard-coded. When the key is missing or Exa errors, the
caller gets WebSearchUnavailable and can carry on without web context.

Manual check: python app/ai/web_search.py "query"
"""
from __future__ import annotations

import logging
import os

from exa_py import Exa

logger = logging.getLogger("monthlymuse.web_search")

DEFAULT_NUM_RESULTS = 5


class WebSearchUnavailable(Exception):
    """Exa is not configured or the search failed."""


def _client() -> Exa:
    api_key = os.environ.get("EXA_API_KEY", "").strip()
    if not api_key:
        raise WebSearchUnavailable("EXA_API_KEY is not set")
    return Exa(api_key=api_key)


def search_web(query: str, num_results: int = DEFAULT_NUM_RESULTS) -> list[dict]:
    """Search the web with Exa and return the top pages with evidence highlights."""
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required")
    try:
        response = _client().search(
            query,
            type="auto",
            num_results=num_results,
            contents={"highlights": True},
        )
    except WebSearchUnavailable:
        raise
    except Exception as exc:  # SDK/transport errors -> one boundary for callers
        raise WebSearchUnavailable(f"Exa search failed: {exc}") from exc

    results = [
        {
            "title": result.title or "",
            "url": result.url,
            "highlights": list(result.highlights or []),
        }
        for result in response.results
    ]
    logger.info("exa_search query=%r results=%d", query, len(results))
    return results


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    import json
    import sys

    logging.basicConfig(level=logging.INFO)
    print(json.dumps(search_web(" ".join(sys.argv[1:]) or "Exa AI web search API"),
                     indent=2, ensure_ascii=False))
