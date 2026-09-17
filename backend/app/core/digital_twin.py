"""
Individualized baseline physiological model ("digital twin").

Per the concept document, risk is measured relative to each subject's own
baseline rather than a population average, because individual anatomical
and physiological variation is a documented factor in conditions like SANS.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core import db
from app.models.schemas import Observation

BASELINE_WINDOW_DAYS = 15  # observations within this many days of enrollment define baseline


@dataclass
class Baseline:
    onsd_mm: float
    globe_flattening_index: float
    hrv_ms: float
    ebv_shedding_copies_ml: float
    cortisol_dhea_ratio: float
    n_observations: int


@dataclass
class DigitalTwin:
    """Holds one crew member's baseline and running observation history."""

    crew_id: str
    history: list[Observation] = field(default_factory=list)
    _baseline: Baseline | None = field(default=None, repr=False)

    def ingest(self, obs: Observation) -> None:
        self.history.append(obs)
        self.history.sort(key=lambda o: o.mission_day)
        self._baseline = None  # invalidate cache; baseline only uses early obs so this is cheap

    def baseline(self) -> Baseline | None:
        if self._baseline is not None:
            return self._baseline
        early = [o for o in self.history if o.mission_day <= BASELINE_WINDOW_DAYS]
        if not early:
            return None

        def avg(attr: str) -> float:
            vals = [getattr(o, attr) for o in early if getattr(o, attr) is not None]
            return sum(vals) / len(vals) if vals else 0.0

        self._baseline = Baseline(
            onsd_mm=avg("onsd_mm"),
            globe_flattening_index=avg("globe_flattening_index"),
            hrv_ms=avg("hrv_ms"),
            ebv_shedding_copies_ml=avg("ebv_shedding_copies_ml"),
            cortisol_dhea_ratio=avg("cortisol_dhea_ratio"),
            n_observations=len(early),
        )
        return self._baseline

    def window(self, up_to_day: int, days: int) -> list[Observation]:
        return [o for o in self.history if up_to_day - days < o.mission_day <= up_to_day]


class TwinRegistry:
    """Registry of digital twins, keyed by crew id.

    Acts as an in-process cache backed by the SQLite (or configured)
    database in `app.core.db` -- the database is the source of truth;
    the in-memory `DigitalTwin` objects are just a per-process cache so
    repeated requests don't re-hit the DB for every computation.
    """

    def __init__(self) -> None:
        self._twins: dict[str, DigitalTwin] = {}

    def get_or_create(self, crew_id: str) -> DigitalTwin:
        if crew_id not in self._twins:
            twin = DigitalTwin(crew_id=crew_id)
            for obs in db.load_observations(crew_id):
                twin.history.append(obs)
            twin.history.sort(key=lambda o: o.mission_day)
            self._twins[crew_id] = twin
        return self._twins[crew_id]

    def has_persisted_history(self, crew_id: str) -> bool:
        return db.has_any_observations(crew_id)

    def ingest(self, obs: Observation) -> None:
        db.save_observation(obs)
        self.get_or_create(obs.crew_id).ingest(obs)


registry = TwinRegistry()
