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

from app.core import i18n
from app.core.domains import DOMAIN_REGISTRY
from app.models.schemas import Countermeasure, CountermeasureRecommendation, RiskOutput


def recommend(risk: RiskOutput, lang: str = "en") -> CountermeasureRecommendation | None:
    lang = i18n.normalize_lang(lang)
    if risk.is_fallback or risk.current_risk <= 0.5 or risk.risk_delta_30d <= 0:
        return None
    domain = DOMAIN_REGISTRY.get(risk.condition)
    if not domain or not domain.countermeasures:
        return None
    choice = domain.countermeasures[0]
    contributors = ", ".join(risk.top_contributors) or i18n.translate(
        i18n.MULTIPLE_SIGNALS_I18N, lang, "multiple signals"
    )
    if lang == i18n.DEFAULT_LANG:
        rationale = (
            f"Suggested because current risk is {risk.current_risk:.0%} and rising "
            f"({risk.risk_delta_30d:+.0%} over 30 days), driven primarily by "
            f"{contributors}. "
            "A human operator must review and decide whether to act on this suggestion."
        )
    else:
        rationale_template = i18n.translate(i18n.COUNTERMEASURE_RATIONALE_TEMPLATE_I18N, lang, "")
        rationale = rationale_template.format(
            risk=risk.current_risk, delta=risk.risk_delta_30d, contributors=contributors
        )
    name = i18n.translate(i18n.COUNTERMEASURE_TEXT.get(choice.id, {}).get("name", {}), lang, choice.name)
    description = i18n.translate(
        i18n.COUNTERMEASURE_TEXT.get(choice.id, {}).get("description", {}), lang, choice.description
    )
    return CountermeasureRecommendation(
        crew_id=risk.crew_id,
        condition=risk.condition,
        countermeasure=Countermeasure(
            id=choice.id, condition=risk.condition, name=name, description=description
        ),
        rationale=rationale,
    )
