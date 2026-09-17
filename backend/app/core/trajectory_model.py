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
require changing its public interface (`score()` -> RiskOutput-shaped dict),
since the API and dashboard are built against that interface, not against
this implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.core.digital_twin import DigitalTwin
from app.models.schemas import Condition, RiskOutput

TRAJECTORY_WINDOW_DAYS = 30
CONFIDENCE_THRESHOLD = 0.55
MIN_OBSERVATIONS_FOR_CONFIDENCE = 4


@dataclass
class SignalSpec:
    """One fused input signal: which field, which direction counts as 'worse',
    how much it should weigh in the fused score, and the clinically-meaningful
    scale used to normalize deviations (deliberately NOT a divide-by-baseline,
    since some signals such as globe flattening have a near-zero baseline)."""

    field: str
    weight: float
    scale: float
    higher_is_worse: bool = True
    label: str = ""


SANS_SIGNALS = [
    SignalSpec("onsd_mm", weight=0.40, scale=1.0, label="optic nerve sheath diameter"),
    SignalSpec("globe_flattening_index", weight=0.30, scale=0.5, label="globe flattening"),
    SignalSpec("hrv_ms", weight=0.15, scale=15.0, higher_is_worse=False, label="heart rate variability"),
    SignalSpec("radiation_dose_msv", weight=0.15, scale=200.0, label="cumulative radiation dose"),
]

IMMUNE_SIGNALS = [
    SignalSpec("ebv_shedding_copies_ml", weight=0.65, scale=300.0, label="EBV viral shedding"),
    SignalSpec("cortisol_dhea_ratio", weight=0.35, scale=0.5, label="cortisol:DHEA ratio"),
]

SIGNAL_SETS: dict[Condition, list[SignalSpec]] = {
    Condition.SANS: SANS_SIGNALS,
    Condition.IMMUNE_DYSREGULATION: IMMUNE_SIGNALS,
}

NARRATIVES = {
    Condition.SANS: {
        True: "Ocular and cardiovascular trends over the last {window} days suggest elevated, worsening SANS-related risk. This is not a diagnosis -- review with the crew medical officer and consider repeat imaging.",
        False: "Ocular and cardiovascular trends over the last {window} days remain within this crew member's expected range for SANS-related risk.",
    },
    Condition.IMMUNE_DYSREGULATION: {
        True: "Viral shedding and stress-hormone trends over the last {window} days suggest elevated immune dysregulation risk. This is not a diagnosis -- consider stress/sleep review and monitor for symptoms.",
        False: "Viral shedding and stress-hormone trends over the last {window} days remain within this crew member's expected range.",
    },
}

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


def score(twin: DigitalTwin, condition: Condition, mission_day: int) -> RiskOutput:
    baseline = twin.baseline()
    signals = SIGNAL_SETS[condition]
    window_obs = twin.window(mission_day, TRAJECTORY_WINDOW_DAYS)

    if baseline is None or len(window_obs) < MIN_OBSERVATIONS_FOR_CONFIDENCE:
        return RiskOutput(
            crew_id=twin.crew_id,
            condition=condition,
            mission_day=mission_day,
            current_risk=0.0,
            risk_delta_30d=0.0,
            confidence=0.0,
            is_fallback=True,
            top_contributors=[],
            narrative=FALLBACK_NARRATIVE,
        )

    days = np.array([o.mission_day for o in window_obs], dtype=float)
    per_signal_contrib: list[tuple[str, float, float]] = []  # (label, weighted deviation, fit)
    fused_level = 0.0
    fused_slope = 0.0
    fit_scores = []

    for spec in signals:
        values = np.array(
            [getattr(o, spec.field) for o in window_obs if getattr(o, spec.field) is not None],
            dtype=float,
        )
        obs_days = np.array(
            [o.mission_day for o in window_obs if getattr(o, spec.field) is not None], dtype=float
        )
        if len(values) < 2:
            continue

        baseline_val = getattr(baseline, spec.field, None)
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
        narrative = NARRATIVES[condition][elevated].format(window=TRAJECTORY_WINDOW_DAYS)

    return RiskOutput(
        crew_id=twin.crew_id,
        condition=condition,
        mission_day=mission_day,
        current_risk=round(current_risk, 3),
        risk_delta_30d=round(risk_delta, 3),
        confidence=round(confidence, 3),
        is_fallback=is_fallback,
        top_contributors=top_contributors if not is_fallback else [],
        narrative=narrative,
    )
