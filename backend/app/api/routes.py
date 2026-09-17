from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core import countermeasures as cm
from app.core import db
from app.core.digital_twin import registry
from app.core.trajectory_model import score
from app.data.synthetic import CREW, MISSION_LENGTH_DAYS, generate_mission_history
from app.models.schemas import (
    Condition,
    CountermeasureLogEntry,
    Observation,
    RiskOutput,
)

router = APIRouter()


def _ensure_seeded(crew_id: str, up_to_day: int) -> None:
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


@router.get("/crew")
def list_crew():
    return CREW


@router.get("/mission/meta")
def mission_meta():
    return {"mission_length_days": MISSION_LENGTH_DAYS, "name": "Mars transit (reference demo)"}


@router.get("/crew/{crew_id}/observations")
def get_observations(crew_id: str, mission_day: int = MISSION_LENGTH_DAYS):
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    return [o.model_dump() for o in twin.history if o.mission_day <= mission_day]


_KNOWN_CREW_IDS = {c.id for c in CREW}


@router.post("/observations", response_model=Observation)
def submit_observation(obs: Observation):
    """Ingest a new (real or manually-entered) observation for a crew member."""
    if obs.crew_id not in _KNOWN_CREW_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown crew_id '{obs.crew_id}'")
    registry.ingest(obs)
    return obs


@router.get("/crew/{crew_id}/risk/{condition}", response_model=RiskOutput)
def get_risk(crew_id: str, condition: Condition, mission_day: int = MISSION_LENGTH_DAYS):
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    if not twin.history:
        raise HTTPException(status_code=404, detail="Unknown crew member")
    return score(twin, condition, mission_day)


@router.get("/crew/{crew_id}/risk", response_model=list[RiskOutput])
def get_all_risk(crew_id: str, mission_day: int = MISSION_LENGTH_DAYS):
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    return [score(twin, c, mission_day) for c in Condition]


@router.get("/crew/{crew_id}/countermeasure/{condition}")
def get_countermeasure(crew_id: str, condition: Condition, mission_day: int = MISSION_LENGTH_DAYS):
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    risk = score(twin, condition, mission_day)
    rec = cm.recommend(risk)
    return rec.model_dump() if rec else None


@router.post("/countermeasures/log")
def log_countermeasure(entry: CountermeasureLogEntry):
    db.save_countermeasure_log(entry)
    return {"logged": True}


@router.get("/countermeasures/log")
def list_countermeasure_log(crew_id: str | None = None):
    return db.load_countermeasure_log(crew_id)
