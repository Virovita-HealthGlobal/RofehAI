from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from app.core import agent as agent_core
from app.core import countermeasures as cm
from app.core import db
from app.core import monitoring
from app.core.digital_twin import registry
from app.core.domains import DOMAIN_REGISTRY, active_domains, all_domains
from app.core.seeding import ensure_seeded as _ensure_seeded
from app.core.trajectory_model import score
from app.data.synthetic import CREW, MISSION_LENGTH_DAYS, generate_mission_history
from app.models.schemas import (
    AgentAskRequest,
    AgentAskResponse,
    CountermeasureLogEntry,
    DomainInfo,
    Observation,
    RiskOutput,
)

router = APIRouter()

# --- Access gate ---------------------------------------------------------
# A simple, single-shared-passcode gate for the dashboard's landing page
# (frontend/gate.html) -- NOT a per-user auth system. It exists to keep
# casual/unintended visitors off a link-shared demo, not to protect
# sensitive data (the demo only ever runs on synthetic data). Real,
# per-user accounts are a separate, larger piece of work -- see README.
GATE_CODE_ENV_VAR = "ROFEHAI_GATE_CODE"
DEFAULT_GATE_CODE = "rofehai-preview-2026"  # change via ROFEHAI_GATE_CODE before real use


@router.post("/gate/check")
def gate_check(payload: dict):
    configured = os.environ.get(GATE_CODE_ENV_VAR) or DEFAULT_GATE_CODE
    submitted = (payload or {}).get("code", "")
    if submitted != configured:
        raise HTTPException(status_code=401, detail="Incorrect access code")
    return {"ok": True}


def _require_domain(domain_id: str) -> None:
    if domain_id not in DOMAIN_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown health domain '{domain_id}'")


@router.get("/crew")
def list_crew():
    return CREW


@router.get("/mission/meta")
def mission_meta():
    return {"mission_length_days": MISSION_LENGTH_DAYS, "name": "Mars transit (reference demo)"}


@router.get("/domains", response_model=list[DomainInfo])
def list_domains():
    """All registered health domains -- both fully active (real synthetic
    scoring) and architecture-ready placeholders. The dashboard uses this
    to render active domains as full risk cards and everything else as
    honestly-labeled 'architecture ready' badges."""
    return [DomainInfo(id=d.id, display_name=d.display_name, status=d.status) for d in all_domains()]


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


@router.get("/crew/{crew_id}/risk/{domain_id}", response_model=RiskOutput)
def get_risk(crew_id: str, domain_id: str, mission_day: int = MISSION_LENGTH_DAYS):
    _require_domain(domain_id)
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    if not twin.history:
        raise HTTPException(status_code=404, detail="Unknown crew member")
    return score(twin, domain_id, mission_day)


@router.get("/crew/{crew_id}/risk", response_model=list[RiskOutput])
def get_all_risk(crew_id: str, mission_day: int = MISSION_LENGTH_DAYS):
    """Risk for every ACTIVE domain (architecture-ready placeholders are
    listed via /api/domains, not scored here -- there's nothing to score)."""
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    return [score(twin, d.id, mission_day) for d in active_domains()]


@router.get("/crew/{crew_id}/countermeasure/{domain_id}")
def get_countermeasure(crew_id: str, domain_id: str, mission_day: int = MISSION_LENGTH_DAYS):
    _require_domain(domain_id)
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    risk = score(twin, domain_id, mission_day)
    rec = cm.recommend(risk)
    return rec.model_dump() if rec else None


@router.post("/agent/ask", response_model=AgentAskResponse)
def agent_ask(payload: AgentAskRequest):
    if payload.crew_id is not None and payload.crew_id not in _KNOWN_CREW_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown crew_id '{payload.crew_id}'")
    try:
        answer, sources, conversation_id = agent_core.run_agent(payload.crew_id, payload.question, payload.conversation_id)
    except agent_core.AgentConversationError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except agent_core.AgentRateLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except agent_core.AgentError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return AgentAskResponse(
        crew_id=payload.crew_id, question=payload.question, answer=answer, sources=sources, conversation_id=conversation_id
    )


@router.get("/agent/usage")
def agent_usage(limit: int = 50):
    """Basic observability into what the agent has actually been asked
    and how it behaved -- real logging, not just 'hope it's fine'. Does
    NOT return conversation content, only call metadata."""
    calls = db.recent_agent_calls(limit)
    return [
        {
            "crew_id": c.crew_id,
            "conversation_id": c.conversation_id,
            "question_length": c.question_length,
            "tool_calls_made": c.tool_calls_made,
            "used_recall": c.used_recall,
            "created_at": c.created_at.isoformat(),
        }
        for c in calls
    ]


# --- Clinical Relay & Response (near-real-time alerting) -----------------
# See backend/app/core/monitoring.py for the detection/severity logic and
# an honest note on what's simulated (the comms link) vs. real (detection,
# packaging, timestamps).

@router.post("/monitoring/check")
def monitoring_check():
    """Run a detection sweep across the whole crew roster now. In a real
    deployment this would run on a schedule or be triggered by new
    observation data; exposed as a manual endpoint here so it can be
    demonstrated and tested directly."""
    new_alerts = monitoring.run_monitoring_sweep()
    return {"new_alerts_count": len(new_alerts), "new_alerts": new_alerts}


@router.get("/alerts")
def list_alerts(status: str | None = None, crew_id: str | None = None, limit: int = 100):
    """status: 'pending' | 'transmitted' | 'acknowledged', or omit for all."""
    alerts = db.list_alerts(status=status, crew_id=crew_id, limit=limit)
    return [monitoring._alert_to_dict(a) for a in alerts]


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: int):
    alert = db.get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Unknown alert id {alert_id}")
    return monitoring._alert_to_dict(alert)


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, payload: dict | None = None):
    """The human-in-the-loop confirmation point: a clinician marks an
    alert as reviewed. This never happens automatically."""
    alert = db.get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Unknown alert id {alert_id}")
    if alert.transmitted_at is None:
        raise HTTPException(status_code=400, detail="Cannot acknowledge an alert that hasn't been transmitted yet")
    note = (payload or {}).get("note")
    updated = db.acknowledge_alert(alert_id, note)
    return monitoring._alert_to_dict(updated)


@router.get("/comms/link")
def get_comms_link():
    state = db.get_comms_link_state()
    return {"available": state.available, "changed_at": state.changed_at.isoformat()}


@router.post("/comms/link")
def set_comms_link(payload: dict):
    """Toggle the SIMULATED comms link. When set to available=true, every
    currently-queued alert transmits immediately, in priority order."""
    available = bool(payload.get("available"))
    state = db.set_comms_link_available(available)
    transmitted = []
    if available:
        transmitted_records = db.transmit_pending_alerts()
        transmitted = [monitoring._alert_to_dict(a) for a in transmitted_records]
    return {
        "available": state.available,
        "changed_at": state.changed_at.isoformat(),
        "transmitted_count": len(transmitted),
        "transmitted": transmitted,
    }


@router.get("/alerts/metrics/latency")
def alert_latency_metrics():
    return db.alert_latency_metrics()


@router.post("/countermeasures/log")
def log_countermeasure(entry: CountermeasureLogEntry):
    db.save_countermeasure_log(entry)
    return {"logged": True}


@router.get("/countermeasures/log")
def list_countermeasure_log(crew_id: str | None = None):
    return db.load_countermeasure_log(crew_id)
