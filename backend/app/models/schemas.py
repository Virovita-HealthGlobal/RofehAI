"""
Pydantic schemas shared across the RofehAI API.

These mirror the entities described in the RofehAI concept document and
draft patent application: an individualized baseline physiological model,
continuous multi-modal observations, a risk-trajectory output, and a
countermeasure recommendation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Condition(str, Enum):
    """Candidate physiological conditions the platform tracks.

    The architecture is condition-agnostic; new conditions are added by
    registering a signal set and a countermeasure table, not by changing
    the core trajectory-modeling or confidence-scoring logic.
    """

    SANS = "sans"  # Spaceflight-Associated Neuro-Ocular Syndrome
    IMMUNE_DYSREGULATION = "immune_dysregulation"


class CrewMember(BaseModel):
    id: str
    name: str
    mission_day_enrolled: int = Field(
        ..., description="Mission day on which pre-flight baseline was established."
    )


class Observation(BaseModel):
    """A single multi-modal reading for one crew member on one mission day."""

    crew_id: str = Field(..., min_length=1, max_length=64)
    mission_day: int = Field(..., ge=0, le=3650)

    # --- SANS-relevant signals ---
    onsd_mm: Optional[float] = Field(
        None, ge=2.0, le=12.0, description="Ultrasound-derived optic nerve sheath diameter, mm."
    )
    globe_flattening_index: Optional[float] = Field(
        None, ge=0.0, le=5.0, description="Unitless index derived from ocular ultrasound; 0 = no flattening."
    )
    hrv_ms: Optional[float] = Field(None, ge=0.0, le=300.0, description="Heart rate variability, ms (SDNN).")
    radiation_dose_msv: Optional[float] = Field(
        None, ge=0.0, le=5000.0, description="Cumulative radiation dose since mission start, mSv."
    )
    sleep_hours: Optional[float] = Field(None, ge=0.0, le=24.0)
    lbnp_sessions_7d: Optional[int] = Field(
        None, ge=0, le=50, description="Lower-body negative pressure countermeasure sessions in trailing 7 days."
    )

    # --- Immune-dysregulation-relevant signals ---
    ebv_shedding_copies_ml: Optional[float] = Field(
        None, ge=0.0, description="EBV DNA copies/mL in saliva (proxy for viral reactivation)."
    )
    cortisol_dhea_ratio: Optional[float] = Field(None, ge=0.0, le=10.0)

    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RiskOutput(BaseModel):
    """The explainable, ranked risk-trajectory output presented to a human operator.

    This intentionally never contains a diagnostic verdict. Fields are named
    to keep language in the "surface / flag / prioritize" register described
    in the concept document's regulatory positioning section, not
    "diagnose / confirm / conclude".
    """

    crew_id: str
    condition: Condition
    mission_day: int

    current_risk: float = Field(..., ge=0, le=1, description="Current estimated risk, 0-1.")
    risk_delta_30d: float = Field(
        ..., description="Change in estimated risk over the trailing 30 mission days."
    )
    confidence: float = Field(..., ge=0, le=1)

    is_fallback: bool = Field(
        False,
        description=(
            "True when confidence fell below threshold and the platform is presenting "
            "a conservative fallback recommendation instead of a specific risk figure."
        ),
    )
    top_contributors: list[str] = Field(
        default_factory=list,
        description="Which input signals contributed most to the change in risk.",
    )
    narrative: str = Field(
        ..., description="Plain-language, non-diagnostic explanation for a non-specialist crew member."
    )


class Countermeasure(BaseModel):
    id: str
    condition: Condition
    name: str
    description: str


class CountermeasureRecommendation(BaseModel):
    crew_id: str
    condition: Condition
    countermeasure: Countermeasure
    rationale: str


class CountermeasureLogEntry(BaseModel):
    crew_id: str
    countermeasure_id: str
    mission_day: int
    notes: Optional[str] = None
