"""
Synthetic longitudinal mission data.

IMPORTANT / HONESTY NOTE
------------------------
RofehAI's real training pipeline is designed to use NASA's publicly
released longitudinal astronaut ophthalmic datasets and head-down-tilt
bed rest studies (the established Earth-based analog for SANS), as
described in the concept document. This sandbox has no network access to
those restricted/registered data portals, so this module generates
clearly-labeled SYNTHETIC data with realistic shapes (progressive ONSD
increase, HRV drift, EBV shedding bursts) purely so the rest of the system
(API, trajectory model, dashboard) can be exercised end to end.

Do not present output derived from this module as a validated clinical
result. Swap `generate_mission_history()` for a real data-loading module
before any real evaluation.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from app.models.schemas import CrewMember, Observation

random.seed(7)

CREW: list[CrewMember] = [
    CrewMember(id="crew-01", name="A. Mendez", mission_day_enrolled=0),
    CrewMember(id="crew-02", name="J. Okafor", mission_day_enrolled=0),
    CrewMember(id="crew-03", name="S. Ito", mission_day_enrolled=0),
    CrewMember(id="crew-04", name="R. Kessler", mission_day_enrolled=0),
]

MISSION_LENGTH_DAYS = 210  # ~ a Mars-transit-length mission
OBS_INTERVAL_DAYS = 3


@dataclass
class SubjectProfile:
    """Per-subject synthetic ground truth, so trajectories differ realistically."""

    sans_susceptibility: float  # 0..1, drives ONSD/globe-flattening slope
    immune_susceptibility: float  # 0..1, drives EBV shedding probability/magnitude
    baseline_onsd: float
    baseline_hrv: float


PROFILES: dict[str, SubjectProfile] = {
    "crew-01": SubjectProfile(sans_susceptibility=0.82, immune_susceptibility=0.35, baseline_onsd=4.9, baseline_hrv=62),
    "crew-02": SubjectProfile(sans_susceptibility=0.30, immune_susceptibility=0.70, baseline_onsd=5.1, baseline_hrv=58),
    "crew-03": SubjectProfile(sans_susceptibility=0.55, immune_susceptibility=0.40, baseline_onsd=5.0, baseline_hrv=65),
    "crew-04": SubjectProfile(sans_susceptibility=0.20, immune_susceptibility=0.25, baseline_onsd=4.8, baseline_hrv=70),
}


def _noise(scale: float) -> float:
    return random.gauss(0, scale)


def generate_mission_history(crew_id: str, up_to_day: int | None = None) -> list[Observation]:
    """Generate a synthetic observation history for one crew member.

    Signals are shaped to loosely mirror documented patterns:
    - ONSD and globe flattening trend upward with mission day, faster for
      higher-susceptibility subjects (mirrors the progressive, duration-linked
      nature of SANS described in the literature).
    - HRV drifts downward under cumulative stress/radiation load.
    - EBV shedding appears as intermittent bursts whose probability rises
      with immune susceptibility and mission duration (mirrors the
      documented rise in herpesvirus shedding frequency on longer missions).
    """
    profile = PROFILES[crew_id]
    last_day = up_to_day if up_to_day is not None else MISSION_LENGTH_DAYS
    history: list[Observation] = []

    lbnp_adherence = random.uniform(0.4, 0.9)

    for day in range(0, last_day + 1, OBS_INTERVAL_DAYS):
        t = day / MISSION_LENGTH_DAYS

        onsd = profile.baseline_onsd + profile.sans_susceptibility * (1.6 * t + 0.3 * t**2) + _noise(0.05)
        globe_flat = max(0.0, profile.sans_susceptibility * (0.8 * t**1.5) + _noise(0.03))
        hrv = profile.baseline_hrv - 10 * t * (1 - 0.3 * lbnp_adherence) + _noise(2.0)
        radiation = 0.9 * day + _noise(3.0)  # ~mSv accumulated, illustrative rate
        sleep = 7.0 - 0.6 * t + _noise(0.4)
        lbnp_sessions = max(0, round(random.gauss(3 * lbnp_adherence, 0.8)))

        shedding_prob = 0.02 + profile.immune_susceptibility * (0.35 * math.sqrt(t))
        ebv = 0.0
        if random.random() < shedding_prob:
            ebv = max(0.0, random.gauss(150 + 400 * profile.immune_susceptibility, 80))
        cortisol_dhea = 0.8 + 0.5 * t * profile.immune_susceptibility + _noise(0.05)

        history.append(
            Observation(
                crew_id=crew_id,
                mission_day=day,
                onsd_mm=round(onsd, 3),
                globe_flattening_index=round(globe_flat, 3),
                hrv_ms=round(max(hrv, 15), 1),
                radiation_dose_msv=round(max(radiation, 0), 1),
                sleep_hours=round(max(sleep, 3.0), 2),
                lbnp_sessions_7d=lbnp_sessions,
                ebv_shedding_copies_ml=round(ebv, 1),
                cortisol_dhea_ratio=round(cortisol_dhea, 3),
            )
        )

    return history
