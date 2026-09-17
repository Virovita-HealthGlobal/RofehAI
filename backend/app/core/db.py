"""
Persistence layer.

Replaces the earlier pure in-memory registry with a real (if simple)
SQLite-backed store, so data survives a restart. Swap `DATABASE_URL` for
a real Postgres URL (e.g. on AWS RDS, per docs/DEPLOYMENT.md) for a real
deployment -- SQLModel/SQLAlchemy make that a one-line change.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlmodel import Field, Session, SQLModel, create_engine, select

DATABASE_URL = os.environ.get("ROFEHAI_DATABASE_URL", "sqlite:///./rofehai.db")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)


class ObservationRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    crew_id: str = Field(index=True)
    mission_day: int

    onsd_mm: float | None = None
    globe_flattening_index: float | None = None
    hrv_ms: float | None = None
    radiation_dose_msv: float | None = None
    sleep_hours: float | None = None
    lbnp_sessions_7d: int | None = None
    ebv_shedding_copies_ml: float | None = None
    cortisol_dhea_ratio: float | None = None

    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CountermeasureLogRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    crew_id: str = Field(index=True)
    countermeasure_id: str
    mission_day: int
    notes: str | None = None
    logged_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def save_observation(obs) -> None:
    with Session(engine) as session:
        record = ObservationRecord(**obs.model_dump(exclude={"recorded_at"}))
        session.add(record)
        session.commit()


def load_observations(crew_id: str):
    from app.models.schemas import Observation  # local import avoids a circular import

    with Session(engine) as session:
        rows = session.exec(
            select(ObservationRecord)
            .where(ObservationRecord.crew_id == crew_id)
            .order_by(ObservationRecord.mission_day)
        ).all()
        return [
            Observation(
                crew_id=r.crew_id,
                mission_day=r.mission_day,
                onsd_mm=r.onsd_mm,
                globe_flattening_index=r.globe_flattening_index,
                hrv_ms=r.hrv_ms,
                radiation_dose_msv=r.radiation_dose_msv,
                sleep_hours=r.sleep_hours,
                lbnp_sessions_7d=r.lbnp_sessions_7d,
                ebv_shedding_copies_ml=r.ebv_shedding_copies_ml,
                cortisol_dhea_ratio=r.cortisol_dhea_ratio,
                recorded_at=r.recorded_at,
            )
            for r in rows
        ]


def has_any_observations(crew_id: str) -> bool:
    with Session(engine) as session:
        row = session.exec(
            select(ObservationRecord).where(ObservationRecord.crew_id == crew_id).limit(1)
        ).first()
        return row is not None


def save_countermeasure_log(entry) -> None:
    with Session(engine) as session:
        record = CountermeasureLogRecord(**entry.model_dump(exclude={"logged_at"}))
        session.add(record)
        session.commit()


def load_countermeasure_log(crew_id: str | None = None):
    with Session(engine) as session:
        stmt = select(CountermeasureLogRecord)
        if crew_id:
            stmt = stmt.where(CountermeasureLogRecord.crew_id == crew_id)
        return session.exec(stmt.order_by(CountermeasureLogRecord.logged_at)).all()
