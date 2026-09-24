"""
Pydantic schemas shared across the RofehAI API.

These mirror the entities described in the RofehAI concept document and
draft patent application: an individualized baseline physiological model,
continuous multi-modal observations, a risk-trajectory output, and a
countermeasure recommendation.

Generalized to a domain-agnostic signal payload (Observation.signals) so
that adding a new health domain (see app.core.domains) does not require a
schema migration -- see docs/ARCHITECTURE.md for the domain registry
pattern this supports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.core.domains import ALL_SIGNAL_SPECS, DOMAIN_REGISTRY


class CrewMember(BaseModel):
    id: str
    name: str
    mission_day_enrolled: int = Field(
        ..., description="Mission day on which pre-flight baseline was established."
    )


class Observation(BaseModel):
    """A single multi-modal reading for one crew member on one mission day.

    `signals` is an open, domain-agnostic key-value payload (e.g.
    {"onsd_mm": 5.1, "hrv_ms": 62.0}). Keys are validated against the
    known signal registry (app.core.domains.ALL_SIGNAL_SPECS) when a
    bound is defined there, so out-of-range or unknown values are still
    rejected -- this preserves the same validation behavior as the
    previous named-field schema, without hardcoding fields here.
    """

    crew_id: str = Field(..., min_length=1, max_length=64)
    mission_day: int = Field(..., ge=0, le=3650)
    signals: dict[str, float] = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("signals")
    @classmethod
    def _validate_signal_bounds(cls, signals: dict[str, float]) -> dict[str, float]:
        for key, value in signals.items():
            spec = ALL_SIGNAL_SPECS.get(key)
            if spec is None:
                continue  # unknown signal keys are allowed (forward-compatible with new domains)
            if spec.min_val is not None and value < spec.min_val:
                raise ValueError(f"signal '{key}'={value} is below the allowed minimum {spec.min_val}")
            if spec.max_val is not None and value > spec.max_val:
                raise ValueError(f"signal '{key}'={value} is above the allowed maximum {spec.max_val}")
        return signals


class RiskOutput(BaseModel):
    """The explainable, ranked risk-trajectory output presented to a human operator.

    This intentionally never contains a diagnostic verdict. Fields are named
    to keep language in the "surface / flag / prioritize" register described
    in the concept document's regulatory positioning section, not
    "diagnose / confirm / conclude".
    """

    crew_id: str
    condition: str = Field(..., description="Health domain id, e.g. 'sans'. See /api/domains for the registry.")
    domain_status: str = Field("active", description="'active' or 'architecture_ready' -- see app.core.domains.")
    mission_day: int

    current_risk: float = Field(..., ge=0, le=1, description="Current estimated risk, 0-1.")
    risk_delta_30d: float = Field(
        ..., description="Change in estimated risk over the trailing 30 mission days."
    )
    confidence: float = Field(..., ge=0, le=1)

    is_fallback: bool = Field(
        False,
        description=(
            "True when confidence fell below threshold (or the domain is architecture-ready "
            "with no signals yet) and the platform is presenting a conservative fallback "
            "instead of a specific risk figure."
        ),
    )
    top_contributors: list[str] = Field(
        default_factory=list,
        description="Which input signals contributed most to the change in risk.",
    )
    narrative: str = Field(
        ..., description="Plain-language, non-diagnostic explanation for a non-specialist crew member."
    )

    @field_validator("condition")
    @classmethod
    def _validate_domain_id(cls, v: str) -> str:
        if v not in DOMAIN_REGISTRY:
            raise ValueError(f"unknown health domain id '{v}'")
        return v


class Countermeasure(BaseModel):
    id: str
    condition: str
    name: str
    description: str


class CountermeasureRecommendation(BaseModel):
    crew_id: str
    condition: str
    countermeasure: Countermeasure
    rationale: str


class CountermeasureLogEntry(BaseModel):
    crew_id: str
    countermeasure_id: str
    mission_day: int
    notes: Optional[str] = None


class DomainInfo(BaseModel):
    """Public-facing summary of one registered health domain, for the
    /api/domains endpoint the dashboard uses to render both fully-active
    risk cards and architecture-ready placeholder badges."""

    id: str
    display_name: str
    status: str


class AgentAskRequest(BaseModel):
    crew_id: str
    question: str = Field(..., min_length=1, max_length=1000)
    conversation_id: Optional[str] = Field(
        None, description="Continue an existing conversation thread. Omit to start a new one."
    )


class AgentSource(BaseModel):
    """One piece of real, fetched data the agent's answer draws from --
    included so the answer is auditable back to an actual RiskOutput or
    countermeasure, not just trusted at face value."""

    domain_id: str
    kind: str  # "risk" | "countermeasure"


class AgentAskResponse(BaseModel):
    crew_id: str
    question: str
    answer: str
    sources: list[AgentSource]
    conversation_id: str = Field(..., description="Pass this back in the next ask() call to continue the thread.")
