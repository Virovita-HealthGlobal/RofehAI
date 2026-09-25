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

DATABASE_URL = os.environ.get("ROFEHAI_DATABASE_URL") or "sqlite:///./data/rofehai.db"

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
    actor_name: str | None = None
    logged_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentConversation(SQLModel, table=True):
    """One persistent conversation thread with the agent, scoped to a
    single crew member. Messages within it give the agent real
    continuity across separate /api/agent/ask calls (use case 2:
    persistent memory), e.g. recalling that a countermeasure was
    discussed two weeks ago without the caller re-stating context."""

    id: str = Field(primary_key=True)  # uuid4 hex
    crew_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentMessage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AgentCallLog(SQLModel, table=True):
    """One row per /api/agent/ask call. Serves two purposes: real
    observability into what the agent is actually doing in production
    (not just 'hope it's fine'), and the source of truth for the daily
    rate limit -- since every call costs real money against the
    Anthropic account, this is a global cap across all crew members, not
    per-crew, because that's how the cost is actually incurred."""

    id: int | None = Field(default=None, primary_key=True)
    crew_id: str = Field(index=True)
    conversation_id: str
    question_length: int
    tool_calls_made: int
    used_recall: bool
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)


class LastKnownRiskState(SQLModel, table=True):
    """The most recent risk state RofehAI observed for one (crew, domain)
    pair, used purely to detect a NEW clinically significant change since
    the last check -- monitoring.py compares each fresh score() result
    against this to decide whether to raise an alert, rather than
    re-alerting on every poll for an already-known elevated state."""

    crew_id: str = Field(primary_key=True)
    domain_id: str = Field(primary_key=True)
    current_risk: float
    is_fallback: bool
    severity: str  # "nominal" | "moderate" | "high"
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AlertRecord(SQLModel, table=True):
    """One clinical alert: a detected, clinically significant change in a
    crew member's trajectory, packaged as a compact evidence package for
    rapid clinician review -- see the Clinical Relay feature. All content
    fields are template-assembled from real RiskOutput data (never free
    LLM text), matching the same discipline as the agent's compose_answer.

    Lifecycle timestamps back the feature's required success metrics:
    detected_at -> queued_at (should be seconds; this IS RofehAI's actual
    clinical-response-time contribution) -> transmitted_at (gated on the
    simulated comms link; this bucket is the UNAVOIDABLE communication
    latency, reported separately, not RofehAI's fault) -> acknowledged_at
    (the human-in-the-loop clinician confirmation point)."""

    id: int | None = Field(default=None, primary_key=True)
    crew_id: str = Field(index=True)
    domain_id: str
    severity: str  # "moderate" | "high"
    current_risk: float
    risk_delta_30d: float
    confidence: float
    narrative: str
    top_contributors_json: str = Field(default="[]")
    pre_authorized_protocol_id: str | None = None

    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    queued_at: datetime | None = None
    transmitted_at: datetime | None = None
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None  # attribution: who acknowledged (lightweight, no accounts -- see README)
    acknowledged_by_note: str | None = None


class User(SQLModel, table=True):
    """A real, individually-attributable account. No public signup --
    accounts are created by an admin (see POST /api/auth/users, protected
    by ROFEHAI_API_KEY) for invited reviewers/collaborators, matching the
    'contact the RofehAI team' language already in the gate. Passwords
    are bcrypt-hashed; the plaintext is never stored or logged anywhere."""

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    display_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_active: bool = Field(default=True)  # allows revoking access without deleting the audit trail


class UserSession(SQLModel, table=True):
    """A real server-side session -- the token is opaque and random, not
    a JWT with embedded claims, so a session can be immediately and
    completely invalidated by deleting this row (revocation is real, not
    just 'wait for expiry')."""

    token: str = Field(primary_key=True)
    user_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime


class GateAccessLog(SQLModel, table=True):
    """Real, server-side record of every login/gate attempt -- the NDA
    acknowledgment, whether it succeeded, and which real user account (if
    any) it was. Does NOT log the submitted password itself, only the
    pass/fail outcome."""

    id: int | None = Field(default=None, primary_key=True)
    username_attempted: str | None = None
    user_id: int | None = None
    nda_acknowledged: bool
    success: bool
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)


class CommsLinkState(SQLModel, table=True):
    """Simulated Earth-spacecraft comms link status -- a stand-in toggle,
    NOT a real radio/network simulation (there is no real spacecraft link
    to model). Single-row table (id is always 1) so the whole system
    shares one link state. When unavailable, alerts queue but do not
    transmit; when toggled available, all pending alerts transmit
    immediately in priority order. See docs/ARCHITECTURE.md."""

    id: int = Field(default=1, primary_key=True)
    available: bool = Field(default=True)
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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


def create_conversation(conversation_id: str, crew_id: str) -> None:
    with Session(engine) as session:
        session.add(AgentConversation(id=conversation_id, crew_id=crew_id))
        session.commit()


def conversation_exists(conversation_id: str) -> bool:
    with Session(engine) as session:
        return session.get(AgentConversation, conversation_id) is not None


def conversation_belongs_to_crew(conversation_id: str, crew_id: str) -> bool:
    with Session(engine) as session:
        convo = session.get(AgentConversation, conversation_id)
        return convo is not None and convo.crew_id == crew_id


def save_agent_message(conversation_id: str, role: str, content: str) -> None:
    with Session(engine) as session:
        session.add(AgentMessage(conversation_id=conversation_id, role=role, content=content))
        session.commit()


def load_agent_messages(conversation_id: str):
    with Session(engine) as session:
        stmt = select(AgentMessage).where(AgentMessage.conversation_id == conversation_id)
        return session.exec(stmt.order_by(AgentMessage.created_at)).all()


def log_agent_call(crew_id: str, conversation_id: str, question_length: int, tool_calls_made: int, used_recall: bool) -> None:
    with Session(engine) as session:
        session.add(
            AgentCallLog(
                crew_id=crew_id,
                conversation_id=conversation_id,
                question_length=question_length,
                tool_calls_made=tool_calls_made,
                used_recall=used_recall,
            )
        )
        session.commit()


def count_agent_calls_since(since: datetime) -> int:
    """Global call count since a given time -- the rate-limit check reads
    this because API cost is incurred per call regardless of which crew
    member it's about."""
    with Session(engine) as session:
        stmt = select(AgentCallLog).where(AgentCallLog.created_at >= since)
        return len(session.exec(stmt).all())


def recent_agent_calls(limit: int = 50):
    with Session(engine) as session:
        stmt = select(AgentCallLog).order_by(AgentCallLog.created_at.desc()).limit(limit)
        return session.exec(stmt).all()


def get_last_known_state(crew_id: str, domain_id: str) -> LastKnownRiskState | None:
    with Session(engine) as session:
        return session.get(LastKnownRiskState, (crew_id, domain_id))


def upsert_last_known_state(crew_id: str, domain_id: str, current_risk: float, is_fallback: bool, severity: str) -> None:
    with Session(engine) as session:
        existing = session.get(LastKnownRiskState, (crew_id, domain_id))
        if existing:
            existing.current_risk = current_risk
            existing.is_fallback = is_fallback
            existing.severity = severity
            existing.checked_at = datetime.now(UTC)
            session.add(existing)
        else:
            session.add(
                LastKnownRiskState(
                    crew_id=crew_id, domain_id=domain_id, current_risk=current_risk,
                    is_fallback=is_fallback, severity=severity,
                )
            )
        session.commit()


def create_alert(
    crew_id: str, domain_id: str, severity: str, current_risk: float, risk_delta_30d: float,
    confidence: float, narrative: str, top_contributors: list[str], pre_authorized_protocol_id: str | None,
) -> AlertRecord:
    with Session(engine) as session:
        alert = AlertRecord(
            crew_id=crew_id, domain_id=domain_id, severity=severity, current_risk=current_risk,
            risk_delta_30d=risk_delta_30d, confidence=confidence, narrative=narrative,
            top_contributors_json=json.dumps(top_contributors),
            pre_authorized_protocol_id=pre_authorized_protocol_id,
        )
        session.add(alert)
        session.commit()
        session.refresh(alert)
        return alert


def mark_alert_queued(alert_id: int) -> AlertRecord | None:
    with Session(engine) as session:
        alert = session.get(AlertRecord, alert_id)
        if alert and alert.queued_at is None:
            alert.queued_at = datetime.now(UTC)
            session.add(alert)
            session.commit()
            session.refresh(alert)
        return alert


def log_gate_attempt(
    nda_acknowledged: bool, success: bool, ip_address: str | None, user_agent: str | None,
    username_attempted: str | None = None, user_id: int | None = None,
) -> None:
    with Session(engine) as session:
        session.add(
            GateAccessLog(
                nda_acknowledged=nda_acknowledged, success=success,
                ip_address=ip_address, user_agent=user_agent,
                username_attempted=username_attempted, user_id=user_id,
            )
        )
        session.commit()


def create_user(username: str, password_hash: str, display_name: str) -> User:
    with Session(engine) as session:
        user = User(username=username, password_hash=password_hash, display_name=display_name)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def get_user_by_username(username: str) -> User | None:
    with Session(engine) as session:
        stmt = select(User).where(User.username == username)
        return session.exec(stmt).first()


def update_user_password(user_id: int, new_password_hash: str) -> None:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user:
            user.password_hash = new_password_hash
            session.add(user)
            session.commit()


def get_user_by_id(user_id: int) -> User | None:
    with Session(engine) as session:
        return session.get(User, user_id)


def list_users():
    with Session(engine) as session:
        return session.exec(select(User).order_by(User.created_at)).all()


def create_session(token: str, user_id: int, expires_at: datetime) -> UserSession:
    with Session(engine) as session:
        s = UserSession(token=token, user_id=user_id, expires_at=expires_at)
        session.add(s)
        session.commit()
        session.refresh(s)
        return s


def get_session(token: str) -> UserSession | None:
    with Session(engine) as session:
        return session.get(UserSession, token)


def delete_session(token: str) -> None:
    with Session(engine) as session:
        s = session.get(UserSession, token)
        if s:
            session.delete(s)
            session.commit()


def recent_gate_attempts(limit: int = 100):
    with Session(engine) as session:
        stmt = select(GateAccessLog).order_by(GateAccessLog.created_at.desc()).limit(limit)
        return session.exec(stmt).all()


def get_comms_link_state() -> CommsLinkState:
    with Session(engine) as session:
        state = session.get(CommsLinkState, 1)
        if state is None:
            state = CommsLinkState(id=1, available=True)
            session.add(state)
            session.commit()
            session.refresh(state)
        return state


def set_comms_link_available(available: bool) -> CommsLinkState:
    with Session(engine) as session:
        state = session.get(CommsLinkState, 1)
        if state is None:
            state = CommsLinkState(id=1, available=available)
        else:
            state.available = available
            state.changed_at = datetime.now(UTC)
        session.add(state)
        session.commit()
        session.refresh(state)
        return state


def transmit_pending_alerts() -> list[AlertRecord]:
    """Transmit every queued-but-not-yet-transmitted alert, in priority
    order (high severity first, then oldest first). Only call this when
    the comms link is actually available -- see routes.py."""
    with Session(engine) as session:
        stmt = select(AlertRecord).where(
            AlertRecord.queued_at.is_not(None), AlertRecord.transmitted_at.is_(None)
        )
        pending = session.exec(stmt).all()
        severity_rank = {"high": 0, "moderate": 1}
        pending_sorted = sorted(pending, key=lambda a: (severity_rank.get(a.severity, 2), a.queued_at))
        now = datetime.now(UTC)
        for alert in pending_sorted:
            alert.transmitted_at = now
            session.add(alert)
        session.commit()
        for alert in pending_sorted:
            session.refresh(alert)
        return pending_sorted


def list_alerts(status: str | None = None, crew_id: str | None = None, limit: int = 100):
    with Session(engine) as session:
        stmt = select(AlertRecord)
        if crew_id:
            stmt = stmt.where(AlertRecord.crew_id == crew_id)
        if status == "pending":
            stmt = stmt.where(AlertRecord.transmitted_at.is_(None))
        elif status == "transmitted":
            stmt = stmt.where(AlertRecord.transmitted_at.is_not(None), AlertRecord.acknowledged_at.is_(None))
        elif status == "acknowledged":
            stmt = stmt.where(AlertRecord.acknowledged_at.is_not(None))
        return session.exec(stmt.order_by(AlertRecord.detected_at.desc()).limit(limit)).all()


def get_alert(alert_id: int) -> AlertRecord | None:
    with Session(engine) as session:
        return session.get(AlertRecord, alert_id)


def acknowledge_alert(alert_id: int, actor_name: str | None, note: str | None) -> AlertRecord | None:
    with Session(engine) as session:
        alert = session.get(AlertRecord, alert_id)
        if alert is None:
            return None
        alert.acknowledged_at = datetime.now(UTC)
        alert.acknowledged_by = actor_name
        alert.acknowledged_by_note = note
        session.add(alert)
        session.commit()
        session.refresh(alert)
        return alert


def alert_latency_metrics():
    """Real, measured latency stats from actual timestamps -- see the
    Clinical Relay feature's success metric requirement: report
    time-to-alert (RofehAI's own processing) SEPARATELY from
    time-to-transmit (comms latency, outside RofehAI's control)."""
    with Session(engine) as session:
        alerts = session.exec(select(AlertRecord)).all()
        detection_to_queue = []
        queue_to_transmit = []
        transmit_to_ack = []
        for a in alerts:
            if a.queued_at:
                detection_to_queue.append((a.queued_at - a.detected_at).total_seconds())
            if a.queued_at and a.transmitted_at:
                queue_to_transmit.append((a.transmitted_at - a.queued_at).total_seconds())
            if a.transmitted_at and a.acknowledged_at:
                transmit_to_ack.append((a.acknowledged_at - a.transmitted_at).total_seconds())

        def _avg(vals):
            return round(sum(vals) / len(vals), 3) if vals else None

        return {
            "total_alerts": len(alerts),
            "avg_seconds_detection_to_alert_queued": _avg(detection_to_queue),
            "avg_seconds_comms_latency_queue_to_transmit": _avg(queue_to_transmit),
            "avg_seconds_transmit_to_clinician_ack": _avg(transmit_to_ack),
            "note": "detection_to_alert_queued is RofehAI's own processing time; "
                    "comms_latency is the unavoidable Earth-link delay, reported separately "
                    "per the feature spec.",
        }
