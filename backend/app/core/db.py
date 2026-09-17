"""
Persistence layer.

SQLite-backed (Postgres-compatible, see docs/DEPLOYMENT.md) store for
observations and countermeasure-log entries.

SCHEMA CHANGE NOTE (domain-registry refactor): ObservationRecord now
stores signals as a JSON-encoded text column instead of one hardcoded
SQL column per signal, matching the generalized Observation.signals
schema. This is a breaking change to any existing database -- since this
is a synthetic-demo-only reference implementation with no real user
data, the recommended migration is a clean reset (delete rofehai.db /
drop and recreate the table) rather than an in-place migration script.
See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import json
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
    signals_json: str = Field(default="{}")
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
        record = ObservationRecord(
            crew_id=obs.crew_id,
            mission_day=obs.mission_day,
            signals_json=json.dumps(obs.signals),
            recorded_at=obs.recorded_at,
        )
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
                signals=json.loads(r.signals_json),
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
