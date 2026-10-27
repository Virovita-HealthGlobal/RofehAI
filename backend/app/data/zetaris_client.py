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

logger = logging.getLogger(__name__)

ZETARIS_ENDPOINT = os.environ.get("ZETARIS_ENDPOINT", "")
ZETARIS_API_KEY  = os.environ.get("ZETARIS_API_KEY", "")

def _zetaris_available() -> bool:
    return bool(ZETARIS_ENDPOINT and ZETARIS_API_KEY)

def query_crew_observations(crew_id: str, up_to_day: int | None = None):
    """
    Fetch longitudinal observations for a crew member.

    Production path  : Zetaris VDL SQL query pushed to source archive.
    Demo/fallback path: synthetic data module.
    """
    if _zetaris_available():
        try:
            import httpx
            sql = (
                f"SELECT * FROM mission_observations "
                f"WHERE crew_id = "{crew_id}" "
                + (f"AND mission_day <= {up_to_day} " if up_to_day else "")
                + "ORDER BY mission_day ASC"
            )
            resp = httpx.post(
                f"{ZETARIS_ENDPOINT}/api/v1/query",
                json={"sql": sql},
                headers={"Authorization": f"Bearer {ZETARIS_API_KEY}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            logger.info("Zetaris: fetched observations for %s", crew_id)
            return resp.json().get("rows", [])
        except Exception as exc:
            logger.warning("Zetaris query failed (%s); using synthetic fallback.", exc)

    # Fallback: synthetic data (demo mode)
    logger.info("Zetaris not configured -- using synthetic data for %s", crew_id)
    from app.data.synthetic import generate_mission_history
    return generate_mission_history(crew_id, up_to_day=up_to_day)
