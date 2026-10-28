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
    raw = query_crew_observations(crew_id, up_to_day=MISSION_LENGTH_DAYS)
    if not raw:
        return
    # Zetaris returns one dict per metric row; synthetic returns Observation objects
    if isinstance(raw[0], dict):
        # Group by mission_day and build Observation objects
        from collections import defaultdict
        from app.models.schemas import Observation
        import datetime
        by_day = defaultdict(dict)
        recorded = {}
        for row in raw:
            day = int(row.get("mission_day", 0))
            metric = row.get("metric", "")
            value = float(row.get("value", 0))
            by_day[day][metric] = value
            recorded[day] = row.get("recorded_at")
        for day, signals in sorted(by_day.items()):
            try:
                rec = recorded.get(day)
                if isinstance(rec, str):
                    rec = datetime.datetime.fromisoformat(rec)
                obs = Observation(
                    crew_id=crew_id,
                    mission_day=day,
                    signals=signals,
                    recorded_at=rec or datetime.datetime.utcnow(),
                )
                registry.ingest(obs)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("skip day %s: %s", day, e)
    else:
        for obs in raw:
            registry.ingest(obs)
