"""Client for the H-MEM service (Meterless H-MEM reference engine, see engines/hmem).

Every call is best-effort: if the memory service is down the agent still answers,
just without cross-session recall. Memory content is only ever built by code from
real, already-fetched data -- never from text the model typed -- so recall cannot
introduce unvetted clinical wording.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

HMEM_URL = os.environ.get("HMEM_URL") or "http://hmem:8910"
HMEM_TOKEN = os.environ.get("HMEM_TOKEN") or ""
TIMEOUT = 4.0


def _headers() -> dict:
    return {"Authorization": f"Bearer {HMEM_TOKEN}"} if HMEM_TOKEN else {}


def remember(content: str, tags: list[str], label: str = "RofehAI briefing") -> str | None:
    """Store one memory. Returns its id, or None if the service is unavailable."""
    try:
        r = httpx.post(
            f"{HMEM_URL}/add",
            json={"content": content, "tags": tags, "source": "rofehai-agent", "label": label, "type": "factual"},
            headers=_headers(), timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("H-MEM remember failed (%s: %s)", type(exc).__name__, exc)
        return None


def recall(query: str, require_tags: list[str], top_n: int = 20) -> list[dict]:
    """Hybrid-ranked recall restricted to memories carrying every tag in require_tags."""
    try:
        r = httpx.post(
            f"{HMEM_URL}/query",
            json={"query": query, "require_tags": require_tags, "top_n": top_n},
            headers=_headers(), timeout=TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("memories", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("H-MEM recall failed (%s: %s)", type(exc).__name__, exc)
        return []
