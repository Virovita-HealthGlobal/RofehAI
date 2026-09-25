"""
Near-real-time clinical relay & response -- detection layer.

WHAT THIS DOES
--------------
Continuously (on-demand sweep, triggered via POST /api/monitoring/check)
compares each crew member's CURRENT trajectory score against their
LAST KNOWN state. When a clinically significant change is detected --
crossing from nominal into an elevated severity tier, or already-elevated
risk continuing to worsen -- it raises an AlertRecord: a compact,
template-assembled evidence package, queued immediately for transmission.

WHY THIS MODULE, NOT THE DASHBOARD/AGENT
------------------------------------------
The dashboard and agent are both PULL-based: a human has to open the
dashboard or ask a question before they see anything. This module is the
PUSH side -- it's what lets RofehAI notice a deterioration and start
preparing the alert before anyone asks, which is the entire point of the
feature (reduce clinical response latency, not physical comms latency).

CONTENT DISCIPLINE (same as trajectory_model.py / agent.py)
--------------------------------------------------------------
Every field in an alert is copied verbatim from a real RiskOutput --
narrative, contributors, confidence, risk figures. There is no free-typed
text generated at alert time. Severity is a deterministic threshold
classification (see HealthDomain.alert_severity_thresholds), not a
judgment call made by an LLM.

WHAT'S SIMULATED, STATED PLAINLY
-----------------------------------
The "comms link" (app.core.db.CommsLinkState) is a manual toggle standing
in for real spacecraft-Earth connectivity -- there is no real radio link
to model in this reference implementation. Detection and packaging are
real; the transmission-gating mechanism is a faithful simulation of the
CONCEPT (alerts queue locally, transmit when a link is available), not a
physical communications system.
"""

from __future__ import annotations

from app.core import db
from app.core.digital_twin import registry
from app.core.domains import DOMAIN_REGISTRY, active_domains
from app.core.seeding import ensure_seeded
from app.core.trajectory_model import score
from app.data.synthetic import CREW, MISSION_LENGTH_DAYS


def classify_severity(current_risk: float, is_fallback: bool, thresholds: dict[str, float]) -> str:
    """Deterministic threshold classification -- never a judgment call."""
    if is_fallback:
        return "nominal"  # insufficient data is not itself an alertable state
    if current_risk >= thresholds.get("high", 0.75):
        return "high"
    if current_risk >= thresholds.get("moderate", 0.5):
        return "moderate"
    return "nominal"


def _is_new_significant_change(previous_severity: str | None, new_severity: str) -> bool:
    """An alert fires when the situation gets WORSE since the last check
    -- crossing into moderate/high for the first time, or moving from
    moderate to high. It does NOT re-fire every sweep just because
    someone is still elevated (that would flood the queue with
    duplicate alerts for a static, already-known state)."""
    if new_severity == "nominal":
        return False
    if previous_severity is None:
        return True  # first time this pair has ever been checked and it's already elevated
    rank = {"nominal": 0, "moderate": 1, "high": 2}
    return rank.get(new_severity, 0) > rank.get(previous_severity, 0)


def run_monitoring_sweep(mission_day: int = MISSION_LENGTH_DAYS) -> list[dict]:
    """Check every crew member across every active domain for a new
    clinically significant change since the last sweep. Returns the
    newly created alerts (as dicts) -- an empty list means nothing new
    was detected, not that nothing was checked."""
    new_alerts = []

    for member in CREW:
        ensure_seeded(member.id, mission_day)
        twin = registry.get_or_create(member.id)

        for domain in active_domains():
            risk = score(twin, domain.id, mission_day)
            new_severity = classify_severity(risk.current_risk, risk.is_fallback, domain.alert_severity_thresholds)

            last_state = db.get_last_known_state(member.id, domain.id)
            previous_severity = last_state.severity if last_state else None

            if _is_new_significant_change(previous_severity, new_severity):
                protocol_id = domain.pre_authorized_protocols[0].id if domain.pre_authorized_protocols else None
                alert = db.create_alert(
                    crew_id=member.id,
                    domain_id=domain.id,
                    severity=new_severity,
                    current_risk=risk.current_risk,
                    risk_delta_30d=risk.risk_delta_30d,
                    confidence=risk.confidence,
                    narrative=risk.narrative,
                    top_contributors=risk.top_contributors,
                    pre_authorized_protocol_id=protocol_id,
                )
                db.mark_alert_queued(alert.id)  # detection -> queue is immediate; this IS the latency RofehAI controls
                fresh_alert = db.get_alert(alert.id)  # re-fetch: mark_alert_queued updated the row, not this local object
                new_alerts.append(_alert_to_dict(fresh_alert))

            db.upsert_last_known_state(member.id, domain.id, risk.current_risk, risk.is_fallback, new_severity)

    return new_alerts


def _alert_to_dict(alert) -> dict:
    import json

    domain = DOMAIN_REGISTRY.get(alert.domain_id)
    protocol = None
    if alert.pre_authorized_protocol_id and domain:
        protocol = next((p for p in domain.pre_authorized_protocols if p.id == alert.pre_authorized_protocol_id), None)

    return {
        "id": alert.id,
        "crew_id": alert.crew_id,
        "domain_id": alert.domain_id,
        "domain_display_name": domain.display_name if domain else alert.domain_id,
        "severity": alert.severity,
        "current_risk": alert.current_risk,
        "risk_delta_30d": alert.risk_delta_30d,
        "confidence": alert.confidence,
        "narrative": alert.narrative,
        "top_contributors": json.loads(alert.top_contributors_json),
        "pre_authorized_protocol": (
            {
                "id": protocol.id,
                "title": protocol.title,
                "instructions": protocol.instructions,
                "authorized_by": protocol.authorized_by,
                "disclaimer": "Pre-authorized protocol reference only -- not autonomous treatment. Clinician confirmation still required.",
            }
            if protocol
            else None
        ),
        "detected_at": alert.detected_at.isoformat() if alert.detected_at else None,
        "queued_at": alert.queued_at.isoformat() if alert.queued_at else None,
        "transmitted_at": alert.transmitted_at.isoformat() if alert.transmitted_at else None,
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "acknowledged_by": alert.acknowledged_by,
        "status": (
            "acknowledged" if alert.acknowledged_at else "transmitted" if alert.transmitted_at else "pending"
        ),
    }
