"""
Trajectory-aware risk fusion model.

REFERENCE IMPLEMENTATION NOTE
------------------------------
The concept document and draft patent describe this component as a
sequence-aware model (e.g. an RNN/transformer/Bayesian state-space model)
trained on real longitudinal data. This module is an explainable,
dependency-light REFERENCE implementation of the same behavior -- it
fits a per-signal trend over a sliding window relative to the subject's
own baseline, fuses the normalized trends into a single risk score, and
computes a confidence score from data density and fit quality -- so the
rest of the platform (API, dashboard, countermeasure loop) can be built
and demonstrated end to end without requiring a trained neural network or
restricted-access datasets.

Swapping this module for a trained sequence model later should not
require changing its public interface (`score()` -> RiskOutput), since
the API and dashboard are built against that interface, not against
this implementation.

DOMAIN-GENERIC NOTE
--------------------
This module reads its signal list, weights, and narrative templates from
the domain registry (app.core.domains) rather than hardcoding them here.
Adding a new domain does not require changing this file -- see
app/core/domains.py.
"""

from __future__ import annotations

import numpy as np

from app.core.digital_twin import DigitalTwin
from app.core.domains import DOMAIN_REGISTRY, NOT_IMPLEMENTED_NARRATIVE, HealthDomain
from app.models.schemas import RiskOutput

TRAJECTORY_WINDOW_DAYS = 30
CONFIDENCE_THRESHOLD = 0.55
MIN_OBSERVATIONS_FOR_CONFIDENCE = 4

FALLBACK_NARRATIVE = (
    "Not enough recent data to produce a confident risk estimate for this crew member. "
    "Recommend collecting an additional measurement before drawing any conclusion."
)


def _slope_and_fit(days: np.ndarray, values: np.ndarray) -> tuple[float, float]:
    """Least-squares slope of `values` over `days`, plus an R^2-like fit quality in [0, 1]."""
    if len(days) < 2 or np.all(values == values[0]):
        return 0.0, 0.0
    A = np.vstack([days, np.ones_like(days)]).T
    slope, intercept = np.linalg.lstsq(A, values, rcond=None)[0]
    predicted = A @ np.array([slope, intercept])
    ss_res = float(np.sum((values - predicted) ** 2))
    ss_tot = float(np.sum((values - np.mean(values)) ** 2))
    fit = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), max(0.0, min(1.0, fit))


def _fallback_output(twin: DigitalTwin, domain: HealthDomain, mission_day: int, narrative: str) -> RiskOutput:
    return RiskOutput(
        crew_id=twin.crew_id,
        condition=domain.id,
        domain_status=domain.status,
        mission_day=mission_day,
        current_risk=0.0,
        risk_delta_30d=0.0,
        confidence=0.0,
        is_fallback=True,
        top_contributors=[],
        narrative=narrative,
    )


def score(twin: DigitalTwin, domain_id: str, mission_day: int) -> RiskOutput:
    domain = DOMAIN_REGISTRY[domain_id]

    # Architecture-ready domains have no signals wired up -- return an
    # explicit "not implemented" output rather than fabricate a score.
    if domain.status != "active" or not domain.signals:
        return _fallback_output(twin, domain, mission_day, NOT_IMPLEMENTED_NARRATIVE)

    baseline = twin.baseline()
    window_obs = twin.window(mission_day, TRAJECTORY_WINDOW_DAYS)

    if baseline is None or len(window_obs) < MIN_OBSERVATIONS_FOR_CONFIDENCE:
        return _fallback_output(twin, domain, mission_day, FALLBACK_NARRATIVE)

    days = np.array([o.mission_day for o in window_obs], dtype=float)
    per_signal_contrib: list[tuple[str, float, float]] = []  # (label, weighted deviation, fit)
    fused_level = 0.0
    fused_slope = 0.0
    fit_scores = []

    for spec in domain.signals:
        values = np.array(
            [o.signals[spec.field] for o in window_obs if spec.field in o.signals],
            dtype=float,
        )
        obs_days = np.array(
            [o.mission_day for o in window_obs if spec.field in o.signals], dtype=float
        )
        if len(values) < 2:
            continue

        baseline_val = baseline.get(spec.field)
        baseline_val = baseline_val if baseline_val is not None else float(np.mean(values))

        slope, fit = _slope_and_fit(obs_days, values)
        current_level = float(values[-1])

        deviation = (current_level - baseline_val) / spec.scale
        if not spec.higher_is_worse:
            deviation = -deviation
            slope = -slope

        weighted_dev = spec.weight * deviation
        fused_level += weighted_dev
        fused_slope += spec.weight * slope
        fit_scores.append(fit)
        per_signal_contrib.append((spec.label, abs(weighted_dev), fit))

    # Squash fused level into a 0-1 risk score.
    current_risk = float(1 / (1 + np.exp(-4 * fused_level)))
    # Express 30-day trend as a risk delta on the same 0-1 scale, sign-preserving.
    risk_delta = float(np.tanh(0.8 * fused_slope * TRAJECTORY_WINDOW_DAYS))

    data_density = min(1.0, len(window_obs) / (TRAJECTORY_WINDOW_DAYS / 3))
    avg_fit = float(np.mean(fit_scores)) if fit_scores else 0.0
    confidence = float(np.clip(0.5 * data_density + 0.5 * avg_fit, 0, 1))

    top_contributors = [
        label for label, _, _ in sorted(per_signal_contrib, key=lambda t: -t[1])[:2]
    ]

    is_fallback = confidence < CONFIDENCE_THRESHOLD
    elevated = current_risk > 0.5 and risk_delta > 0

    if is_fallback:
        narrative = FALLBACK_NARRATIVE
    else:
        template = domain.narrative_elevated if elevated else domain.narrative_stable
        narrative = template.format(window=TRAJECTORY_WINDOW_DAYS)

    return RiskOutput(
        crew_id=twin.crew_id,
        condition=domain.id,
        domain_status=domain.status,
        mission_day=mission_day,
        current_risk=round(current_risk, 3),
        risk_delta_30d=round(risk_delta, 3),
        confidence=round(confidence, 3),
        is_fallback=is_fallback,
        top_contributors=top_contributors if not is_fallback else [],
        narrative=narrative,
    )
