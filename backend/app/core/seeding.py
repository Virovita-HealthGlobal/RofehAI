"""
Shared crew-history seeding helper.

Extracted from routes.py so both the REST API and the agent orchestration
layer (app.core.agent) can ensure a crew member's synthetic history exists
without importing from routes.py (which would create a circular import,
since routes.py also needs to call into the agent layer).
"""

from __future__ import annotations

from app.core.digital_twin import registry
from app.data.synthetic import MISSION_LENGTH_DAYS
from app.data.zetaris_client import query_crew_observations


def ensure_seeded(crew_id: str, up_to_day: int) -> None:
    """Lazily populate a crew member's digital twin with history
    the first time they're requested. Routes through zetaris_client,
    which queries Zetaris VDL in production and falls back to synthetic
    data when no Zetaris endpoint is configured (demo mode).
    Always seeds the FULL mission length so later queries are not starved.
    Persists to the DB so this only happens once per crew member, ever."""
    if registry.has_persisted_history(crew_id):
        return
    for obs in query_crew_observations(crew_id, up_to_day=MISSION_LENGTH_DAYS):
        registry.ingest(obs)
