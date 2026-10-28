"""
zetaris_client.py  --  Zetaris federated data layer for RofehAI
================================================================
Zetaris enables federated queries: data is queried where it lives
(hospital EHR, research archive, wearable telemetry) without moving
it to a central store.

In production this module connects to a Zetaris Virtual Data Layer
endpoint and issues SQL queries that Zetaris pushes down to the
source systems. In this demo environment it falls back to the
synthetic data module when no Zetaris endpoint is configured.

Open Agent Hackathon 2026 -- Data layer sponsor: Zetaris
"""
import logging
import os
import re

logger = logging.getLogger(__name__)

ZETARIS_ENDPOINT = os.environ.get("ZETARIS_ENDPOINT", "")
ZETARIS_API_KEY  = os.environ.get("ZETARIS_API_KEY", "")
ZETARIS_ORG_ID   = os.environ.get("ZETARIS_ORG_ID", "")

def _zetaris_available() -> bool:
    return bool(ZETARIS_ENDPOINT and ZETARIS_API_KEY)


# Map app crew IDs to Zetaris CSV crew IDs
CREW_ID_MAP = {
    "crew-01": "chen_wei",
    "crew-02": "aisha_okonkwo",
    "crew-03": "elena_vasquez",
    "crew-04": "marcus_johnson",
    "crew-05": "yuki_tanaka",
}

def _validate_crew_id(crew_id: str) -> str:
    """Restrict crew_id to safe alphanumeric/dash/underscore values."""
    if not re.match(r'^[a-zA-Z0-9_-]+$', crew_id):
        raise ValueError(f"Invalid crew_id: {crew_id!r}")
    return crew_id

def query_crew_observations(crew_id: str, up_to_day: int | None = None):
    """
    Fetch longitudinal observations for a crew member.

    Production path  : Zetaris VDL SQL query pushed to source archive.
    Demo/fallback path: synthetic data module.
    """
    if _zetaris_available():
        try:
            import httpx
            mapped_id = CREW_ID_MAP.get(crew_id, crew_id)
            safe_crew_id = _validate_crew_id(mapped_id)
            logger.info("Zetaris: querying crew_id=%s (mapped from %s)", safe_crew_id, crew_id)
            sql = (
                f"SELECT * FROM ROFEHAI.mission_observations "
                f"WHERE crew_id = '{safe_crew_id}' "
                + (f"AND mission_day <= {int(up_to_day)} " if up_to_day else "")
                + "ORDER BY mission_day ASC"
            )
            headers = {
                "Authorization": f"Bearer {ZETARIS_API_KEY}",
                "Content-Type": "application/json",
                "ngrok-skip-browser-warning": "true",
            }
            if ZETARIS_ORG_ID:
                headers["X-Org-ID"] = ZETARIS_ORG_ID
            import uuid as _uuid
            resp = httpx.post(
                f"{ZETARIS_ENDPOINT}/api/proxy/sql-editor/sqls/run-stream",
                json={"sql": sql, "queryId": str(_uuid.uuid4()), "chunkSize": 1000, "source": "SqlEditor"},
                headers=headers,
                timeout=120.0,
            )
            resp.raise_for_status()
            lines = [l.strip() for l in resp.text.strip().splitlines() if l.strip()]
            if not lines:
                raise ValueError("empty response")
            import json as _json
            columns = _json.loads(lines[0])
            rows = []
            for line in lines[1:]:
                try:
                    rows.append(dict(zip(columns, _json.loads(line))))
                except Exception:
                    continue
            logger.info("Zetaris: fetched %d observations for %s", len(rows), crew_id)
            return rows
        except Exception as exc:
            logger.warning("Zetaris query failed (%s: %s); using synthetic fallback.", type(exc).__name__, exc, exc_info=True)

    # Fallback: synthetic data (demo mode)
    logger.info("Zetaris not configured -- using synthetic data for %s", crew_id)
    from app.data.synthetic import generate_mission_history
    return generate_mission_history(crew_id, up_to_day=up_to_day)
