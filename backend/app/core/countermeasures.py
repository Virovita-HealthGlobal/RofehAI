"""
Countermeasure catalog and recommendation logic.

Per the concept document's regulatory positioning, this module SUGGESTS a
candidate countermeasure from a constrained, known inventory -- it never
autonomously administers or schedules one. A human always chooses whether
to act on the suggestion, and logging a countermeasure as used is a
separate, explicit human action (see /api/countermeasures/log).
"""

from __future__ import annotations

from app.models.schemas import Condition, Countermeasure, CountermeasureRecommendation, RiskOutput

CATALOG: dict[Condition, list[Countermeasure]] = {
    Condition.SANS: [
        Countermeasure(
            id="lbnp-protocol",
            condition=Condition.SANS,
            name="Lower-body negative pressure (LBNP) sessions",
            description=(
                "Mechanical countermeasure under active tolerability evaluation for SANS; "
                "redistributes cephalad fluid shift during scheduled sessions."
            ),
        ),
        Countermeasure(
            id="exercise-adjust",
            condition=Condition.SANS,
            name="Exercise protocol adjustment",
            description="Adjust resistance/cardio balance per crew flight surgeon guidance.",
        ),
    ],
    Condition.IMMUNE_DYSREGULATION: [
        Countermeasure(
            id="sleep-stress-review",
            condition=Condition.IMMUNE_DYSREGULATION,
            name="Sleep and stress-load review",
            description="Structured review of sleep debt and psychological stress load with CMO.",
        ),
        Countermeasure(
            id="clinical-referral",
            condition=Condition.IMMUNE_DYSREGULATION,
            name="Flag for asynchronous flight-surgeon review",
            description="No onboard pharmacological countermeasure is established; escalate for review.",
        ),
    ],
}


def recommend(risk: RiskOutput) -> CountermeasureRecommendation | None:
    if risk.is_fallback or risk.current_risk <= 0.5 or risk.risk_delta_30d <= 0:
        return None
    options = CATALOG.get(risk.condition, [])
    if not options:
        return None
    choice = options[0]
    rationale = (
        f"Suggested because current risk is {risk.current_risk:.0%} and rising "
        f"({risk.risk_delta_30d:+.0%} over 30 days), driven primarily by "
        f"{', '.join(risk.top_contributors) or 'multiple signals'}. "
        "A human operator must review and decide whether to act on this suggestion."
    )
    return CountermeasureRecommendation(
        crew_id=risk.crew_id, condition=risk.condition, countermeasure=choice, rationale=rationale
    )
