"""
Countermeasure recommendation logic.

Per the concept document's regulatory positioning, this module SUGGESTS a
candidate countermeasure from a constrained, known inventory -- it never
autonomously administers or schedules one. A human always chooses whether
to act on the suggestion, and logging a countermeasure as used is a
separate, explicit human action (see /api/countermeasures/log).

The actual catalog now lives per-domain in app.core.domains, so this
module has no per-condition special-casing.
"""

from __future__ import annotations

from app.core.domains import DOMAIN_REGISTRY
from app.models.schemas import Countermeasure, CountermeasureRecommendation, RiskOutput


def recommend(risk: RiskOutput) -> CountermeasureRecommendation | None:
    if risk.is_fallback or risk.current_risk <= 0.5 or risk.risk_delta_30d <= 0:
        return None
    domain = DOMAIN_REGISTRY.get(risk.condition)
    if not domain or not domain.countermeasures:
        return None
    choice = domain.countermeasures[0]
    rationale = (
        f"Suggested because current risk is {risk.current_risk:.0%} and rising "
        f"({risk.risk_delta_30d:+.0%} over 30 days), driven primarily by "
        f"{', '.join(risk.top_contributors) or 'multiple signals'}. "
        "A human operator must review and decide whether to act on this suggestion."
    )
    return CountermeasureRecommendation(
        crew_id=risk.crew_id,
        condition=risk.condition,
        countermeasure=Countermeasure(
            id=choice.id, condition=risk.condition, name=choice.name, description=choice.description
        ),
        rationale=rationale,
    )
