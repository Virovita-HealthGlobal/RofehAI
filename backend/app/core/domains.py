"""
Health domain registry.

This is the module that makes RofehAI "one platform, multiple health
domains" rather than a collection of separate prediction engines. Adding
a new domain means adding one entry here -- a signal list, narrative
templates, and a countermeasure catalog -- not touching the trajectory
model, the API routes, or the frontend's rendering loop.

Two domain statuses:
- "active":            has real (synthetic-for-now) signals wired up and
                        produces real trajectory scoring.
- "architecture_ready": exists in the registry and shows up in the
                        platform's domain list, but has no signals wired
                        yet. Scoring such a domain returns an explicit
                        "not yet implemented" output rather than a
                        fabricated number -- see trajectory_model.score().

IMPORTANT: "architecture_ready" domains are placeholders, not clinical
claims. Do not wire up fabricated performance for them -- see the
concept document's section on avoiding unsupported medical claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DomainStatus = Literal["active", "architecture_ready"]


@dataclass
class SignalSpec:
    """One fused input signal for a domain's trajectory model: which
    field in Observation.signals it reads, how much it weighs in the
    fused score, the clinically-meaningful scale used to normalize
    deviations, and optional validation bounds enforced on submission."""

    field: str
    weight: float
    scale: float
    higher_is_worse: bool = True
    label: str = ""
    min_val: float | None = None
    max_val: float | None = None


@dataclass
class CountermeasureSpec:
    id: str
    name: str
    description: str


@dataclass
class HealthDomain:
    id: str
    display_name: str
    status: DomainStatus
    signals: list[SignalSpec] = field(default_factory=list)
    narrative_elevated: str = ""
    narrative_stable: str = ""
    countermeasures: list[CountermeasureSpec] = field(default_factory=list)


NOT_IMPLEMENTED_NARRATIVE = (
    "Architecture ready -- no synthetic signals implemented for this domain yet. "
    "This is a placeholder in the platform's domain registry, not a clinical output."
)


_DOMAINS: list[HealthDomain] = [
    HealthDomain(
        id="sans",
        display_name="SANS (Neuro-Ocular)",
        status="active",
        signals=[
            SignalSpec("onsd_mm", weight=0.40, scale=1.0, label="optic nerve sheath diameter", min_val=2.0, max_val=12.0),
            SignalSpec("globe_flattening_index", weight=0.30, scale=0.5, label="globe flattening", min_val=0.0, max_val=5.0),
            SignalSpec("hrv_ms", weight=0.15, scale=15.0, higher_is_worse=False, label="heart rate variability", min_val=0.0, max_val=300.0),
            SignalSpec("radiation_dose_msv", weight=0.15, scale=200.0, label="cumulative radiation dose", min_val=0.0, max_val=5000.0),
        ],
        narrative_elevated=(
            "Ocular and cardiovascular trends over the last {window} days suggest elevated, "
            "worsening SANS-related risk. This is not a diagnosis -- review with the crew "
            "medical officer and consider repeat imaging."
        ),
        narrative_stable=(
            "Ocular and cardiovascular trends over the last {window} days remain within "
            "this crew member's expected range for SANS-related risk."
        ),
        countermeasures=[
            CountermeasureSpec(
                id="lbnp-protocol",
                name="Lower-body negative pressure (LBNP) sessions",
                description=(
                    "Mechanical countermeasure under active tolerability evaluation for SANS; "
                    "redistributes cephalad fluid shift during scheduled sessions."
                ),
            ),
            CountermeasureSpec(
                id="exercise-adjust",
                name="Exercise protocol adjustment",
                description="Adjust resistance/cardio balance per crew flight surgeon guidance.",
            ),
        ],
    ),
    HealthDomain(
        id="immune_dysregulation",
        display_name="Immune Dysregulation",
        status="active",
        signals=[
            SignalSpec("ebv_shedding_copies_ml", weight=0.65, scale=300.0, label="EBV viral shedding", min_val=0.0),
            SignalSpec("cortisol_dhea_ratio", weight=0.35, scale=0.5, label="cortisol:DHEA ratio", min_val=0.0, max_val=10.0),
        ],
        narrative_elevated=(
            "Viral shedding and stress-hormone trends over the last {window} days suggest "
            "elevated immune dysregulation risk. This is not a diagnosis -- consider "
            "stress/sleep review and monitor for symptoms."
        ),
        narrative_stable=(
            "Viral shedding and stress-hormone trends over the last {window} days remain "
            "within this crew member's expected range."
        ),
        countermeasures=[
            CountermeasureSpec(
                id="sleep-stress-review",
                name="Sleep and stress-load review",
                description="Structured review of sleep debt and psychological stress load with CMO.",
            ),
            CountermeasureSpec(
                id="clinical-referral",
                name="Flag for asynchronous flight-surgeon review",
                description="No onboard pharmacological countermeasure is established; escalate for review.",
            ),
        ],
    ),
    # --- Architecture-ready placeholder domains ---------------------------
    # No signals, no scoring -- exist so the platform's domain list and
    # dashboard can show the intended multi-system scope honestly, without
    # fabricating clinical behavior for domains that aren't built yet.
    HealthDomain(id="cardiovascular", display_name="Cardiovascular Adaptation", status="architecture_ready"),
    HealthDomain(id="musculoskeletal", display_name="Musculoskeletal (Bone & Muscle)", status="architecture_ready"),
    HealthDomain(id="radiation", display_name="Radiation Exposure", status="architecture_ready"),
    HealthDomain(id="sleep_circadian", display_name="Sleep & Circadian", status="architecture_ready"),
    HealthDomain(id="cognitive", display_name="Cognitive Performance", status="architecture_ready"),
    HealthDomain(id="behavioral", display_name="Behavioral / Wellbeing", status="architecture_ready"),
    HealthDomain(id="emergency_general", display_name="Emergency / General Medical", status="architecture_ready"),
]

DOMAIN_REGISTRY: dict[str, HealthDomain] = {d.id: d for d in _DOMAINS}

# Every known signal field across every domain, for Observation-level
# validation (bounds enforcement) without duplicating bounds in the schema.
ALL_SIGNAL_SPECS: dict[str, SignalSpec] = {
    spec.field: spec for domain in _DOMAINS for spec in domain.signals
}


def active_domains() -> list[HealthDomain]:
    return [d for d in _DOMAINS if d.status == "active"]


def all_domains() -> list[HealthDomain]:
    return list(_DOMAINS)
