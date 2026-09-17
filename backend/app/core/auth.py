"""
Minimal API-key authentication.

This is intentionally simple -- a single shared secret via header, not a
full auth system (no per-user identity, no scopes, no token expiry). It
exists so the API isn't wide open by default once deployed anywhere real,
while staying trivial to run locally for development.

Set ROFEHAI_API_KEY in the environment to enable enforcement. If it's
unset, the API runs with auth DISABLED -- convenient for local dev, but
you must set it before deploying anywhere reachable outside your own
machine. See docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException

API_KEY_ENV_VAR = "ROFEHAI_API_KEY"


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    configured_key = os.environ.get(API_KEY_ENV_VAR)
    if not configured_key:
        # No key configured -- auth disabled (local/dev mode only).
        return
    if x_api_key != configured_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")
