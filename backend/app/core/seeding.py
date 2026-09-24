"""
Shared crew-history seeding helper.

Extracted from routes.py so both the REST API and the agent orchestration
layer (app.core.agent) can ensure a crew member's synthetic history exists
without importing from routes.py (which would create a circular import,
since routes.py also needs to call into the agent layer).
"""

from __future__ import annotations

from app.core.digital_twin import registry
from app.data.synthetic import MISSION_LENGTH_DAYS, generate_mission_history


def ensure_seeded(crew_id: str, up_to_day: int) -> None:
    """Lazily populate a crew member's digital twin with synthetic history
    the first time they're requested, so the API is usable immediately
    without a separate seeding step. Always seeds the FULL mission length
    (not just up to the first-requested day) so that later queries for
    later mission days aren't starved of data. Persists to the DB so this
    only happens once per crew member, ever -- not once per process."""
    if registry.has_persisted_history(crew_id):
        return
    for obs in generate_mission_history(crew_id, up_to_day=MISSION_LENGTH_DAYS):
        registry.ingest(obs)
