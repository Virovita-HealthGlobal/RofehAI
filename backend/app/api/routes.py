from __future__ import annotations

import os
import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.core import agent as agent_core
from app.core import countermeasures as cm
from app.core import db
from app.core import monitoring
from app.core import notify
from app.core import user_auth
from app.core.digital_twin import registry
from app.core.domains import DOMAIN_REGISTRY, active_domains, all_domains
from app.core.seeding import ensure_seeded as _ensure_seeded
from app.core.trajectory_model import score
from app.data.synthetic import CREW, MISSION_LENGTH_DAYS, generate_mission_history
from app.models.schemas import (
    AgentAskRequest,
    AgentAskResponse,
    CountermeasureLogEntry,
    DomainInfo,
    Observation,
    RiskOutput,
)

router = APIRouter()

# --- Access gate ---------------------------------------------------------
# A simple, single-shared-passcode gate for the dashboard's landing page
# (frontend/gate.html) -- NOT a per-user auth system. It exists to keep
# casual/unintended visitors off a link-shared demo, not to protect
# sensitive data (the demo only ever runs on synthetic data). Real,
# per-user accounts are a separate, larger piece of work -- see README.
GATE_CODE_ENV_VAR = "ROFEHAI_GATE_CODE"
DEFAULT_GATE_CODE = "rofehai-preview-2026"  # change via ROFEHAI_GATE_CODE before real use


@router.post("/gate/check")
def gate_check(payload: dict, request: Request):
    configured = os.environ.get(GATE_CODE_ENV_VAR) or DEFAULT_GATE_CODE
    submitted = (payload or {}).get("code", "")
    nda_acknowledged = bool((payload or {}).get("nda_acknowledged", False))
    success = submitted == configured

    # Real, server-side audit trail -- see db.GateAccessLog. Logged
    # regardless of outcome, so failed attempts are visible too. Behind
    # Cloudflare's proxy, the real client IP is in X-Forwarded-For, not
    # request.client.host (which would just be Cloudflare's own edge IP).
    forwarded_for = request.headers.get("x-forwarded-for")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else None)
    db.log_gate_attempt(
        nda_acknowledged=nda_acknowledged,
        success=success,
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )

    if not success:
        raise HTTPException(status_code=401, detail="Incorrect access code")
    return {"ok": True}


@router.get("/gate/audit-log")
def gate_audit_log(limit: int = 100):
    """Real observability into gate access attempts -- WHO tried (both
    the attempted username and, on success, the real display name), when,
    whether they acknowledged the NDA, whether the login succeeded.
    Never returns the submitted password itself."""
    attempts = db.recent_gate_attempts(limit)
    result = []
    for a in attempts:
        display_name = None
        if a.user_id:
            user = db.get_user_by_id(a.user_id)
            display_name = user.display_name if user else None
        result.append(
            {
                "username_attempted": a.username_attempted,
                "display_name": display_name,  # None for a failed attempt (no user was ever resolved) or an anonymous/unauthenticated hit
                "user_id": a.user_id,
                "nda_acknowledged": a.nda_acknowledged,
                "success": a.success,
                "ip_address": a.ip_address,
                "user_agent": a.user_agent,
                "created_at": a.created_at.isoformat(),
            }
        )
    return result


# --- Real per-user authentication -----------------------------------------
# Replaces the shared-passcode gate's model (anyone who knows the secret is
# indistinguishable from anyone else) with actual accounts. No public
# signup -- see user_auth.py's module docstring.

ADMIN_KEY_ENV_VAR = "ROFEHAI_ADMIN_KEY"


def _require_admin_key(x_api_key: str | None = Header(default=None)) -> None:
    """Deliberately a SEPARATE env var from ROFEHAI_API_KEY (which, if
    set, requires every /api/* request to carry it). Reusing that same
    variable here would mean an admin setting a key just to create
    accounts accidentally locks the entire public dashboard behind that
    same key too -- a real bug caught by actually running the full login
    flow end to end, not just unit tests. FAILS CLOSED if unset (unlike
    the general API key, which defaults open for local-dev convenience)
    -- account creation is more sensitive than general read access."""
    configured_key = os.environ.get(ADMIN_KEY_ENV_VAR)
    if not configured_key:
        raise HTTPException(
            status_code=503,
            detail=f"{ADMIN_KEY_ENV_VAR} must be set on the server to create accounts.",
        )
    if x_api_key != configured_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")


def get_current_user(authorization: str | None = Header(default=None)):
    """FastAPI dependency: resolves the real authenticated user from a
    'Authorization: Bearer <token>' header, or None if absent/invalid/
    expired. Callers that require a real user (not just 'someone with
    the passcode') should check for None themselves -- see
    acknowledge_alert and log_countermeasure below for how attribution
    uses this without hard-requiring login everywhere yet."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):]
    return user_auth.resolve_session(token)


@router.post("/auth/users")
def create_user_account(payload: dict, x_api_key: str | None = Header(default=None)):
    _require_admin_key(x_api_key)
    username = (payload or {}).get("username", "").strip()
    password = (payload or {}).get("password", "")
    display_name = (payload or {}).get("display_name", "").strip() or username
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password are required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    if db.get_user_by_username(username):
        raise HTTPException(status_code=409, detail=f"username '{username}' already exists")
    user = db.create_user(username, user_auth.hash_password(password), display_name)
    return {"id": user.id, "username": user.username, "display_name": user.display_name}


@router.get("/auth/users")
def list_user_accounts(x_api_key: str | None = Header(default=None)):
    _require_admin_key(x_api_key)
    users = db.list_users()
    return [{"id": u.id, "username": u.username, "display_name": u.display_name, "is_active": u.is_active} for u in users]


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_SET_TOKEN_LIFETIME_HOURS = 48


@router.post("/contact")
def contact(payload: dict):
    """General-inquiry contact form on the marketing landing page -- no
    account involved, just a forwarded message. Same email validation as
    signup, since we need a real reply-to address."""
    name = (payload or {}).get("name", "").strip()
    email = (payload or {}).get("email", "").strip().lower()
    message = (payload or {}).get("message", "").strip()

    if not name or not email or not message:
        raise HTTPException(status_code=400, detail="Name, email, and message are all required")
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="That doesn't look like a valid email address")
    if len(message) > 5000:
        raise HTTPException(status_code=400, detail="Message is too long")

    try:
        notify.send_contact_message(name, email, message)
    except notify.EmailError as e:
        raise HTTPException(status_code=502, detail=f"Message could not be sent: {e}")

    return {"ok": True, "message": "Message sent. We'll get back to you."}


@router.post("/auth/signup")
def signup(payload: dict):
    """Self-service signup -- collects ONLY a professional email and NDA
    acknowledgment. No password here at all: the account is created
    pending and inactive, and stays that way until an admin approves AND
    the requester sets their own password via a link sent to their own
    inbox. The NDA checkbox is required here (not optional, unlike the
    login endpoint's flag, which just logs it) -- signup is exactly the
    moment that acknowledgment matters."""
    email = (payload or {}).get("email", "").strip().lower()
    display_name = (payload or {}).get("display_name", "").strip() or email
    nda_acknowledged = bool((payload or {}).get("nda_acknowledged", False))

    if not email:
        raise HTTPException(status_code=400, detail="A professional email address is required")
    if not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=400, detail="That doesn't look like a valid email address")
    if not nda_acknowledged:
        raise HTTPException(status_code=400, detail="NDA acknowledgment is required to request access")
    if db.get_user_by_username(email):
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    token = secrets.token_urlsafe(32)
    user = db.create_pending_signup(email, display_name, token)

    base_url = os.environ.get("ROFEHAI_PUBLIC_API_BASE", "").rstrip("/")
    approve_url = f"{base_url}/api/auth/approve/{token}"
    reject_url = f"{base_url}/api/auth/reject/{token}"

    try:
        notify.send_signup_approval_email(email, display_name, user.nda_acknowledged_at, approve_url, reject_url)
    except notify.EmailError as e:
        # The account still exists as pending -- an admin can approve it
        # manually later even if the notification email itself failed to
        # send. Surface the failure rather than silently pretending it worked.
        raise HTTPException(status_code=502, detail=f"Signup recorded, but the admin notification email failed to send: {e}")

    return {"ok": True, "message": "Signup received. An admin will review your request."}


@router.get("/auth/approve/{token}", response_class=HTMLResponse)
def approve_signup(token: str):
    """Approving does NOT activate the account -- it issues a single-use
    6-digit code and emails it to the REQUESTER, who enters it alongside
    their email to set their own password. The admin never sees or sets
    a password for anyone."""
    user = db.get_user_by_approval_token(token)
    if user is None:
        return HTMLResponse("<h2>This approval link is invalid or has already been used.</h2>", status_code=404)

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=PASSWORD_SET_TOKEN_LIFETIME_HOURS)
    db.approve_pending_user(user.id, code, expires_at)

    frontend_base = os.environ.get("ROFEHAI_PUBLIC_APP_BASE", "").rstrip("/")
    set_password_url = f"{frontend_base}/set-password.html"

    try:
        notify.send_password_setup_email(user.username, user.display_name, set_password_url, code, is_reset=False)
        return HTMLResponse(f"<h2>Approved. A password-setup code has been emailed to {user.username}.</h2>")
    except notify.EmailError as e:
        return HTMLResponse(
            f"<h2>Approved, but the email to {user.username} failed to send: {e}</h2>"
            f"<p>The account is waiting for a password. You may need to resend this manually.</p>"
        )


@router.get("/auth/reject/{token}", response_class=HTMLResponse)
def reject_signup(token: str):
    user = db.get_user_by_approval_token(token)
    if user is None:
        return HTMLResponse("<h2>This link is invalid or has already been used.</h2>", status_code=404)
    username = user.username
    db.reject_pending_user(user.id)
    return HTMLResponse(f"<h2>Rejected. The request for '{username}' has been removed.</h2>")


@router.post("/auth/set-password")
def set_password(payload: dict):
    """Used both right after approval (first password ever) and for a
    later 'forgot password' reset -- identical mechanism, identical
    guarantees: single-use, expiring 6-digit code, requires the matching
    email too (a code alone has far less entropy than the old 32-byte
    URL token, so pairing it with a known email narrows what an
    attacker would need to guess correctly)."""
    email = (payload or {}).get("email", "").strip().lower()
    code = (payload or {}).get("code", "").strip()
    new_password = (payload or {}).get("new_password", "")

    user = db.get_user_by_username_and_password_set_code(email, code)
    if user is None:
        raise HTTPException(status_code=404, detail="That code doesn't match, or has already been used")
    if user.password_set_token_expires_at and user.password_set_token_expires_at < datetime.now(UTC).replace(tzinfo=None):
        raise HTTPException(status_code=410, detail="This code has expired. Request a new one.")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    db.set_user_password_and_activate(user.id, user_auth.hash_password(new_password))
    return {"ok": True, "message": "Password set. You can now sign in."}


@router.post("/auth/forgot-password")
def forgot_password(payload: dict):
    """Always returns the same generic response regardless of whether
    the email matched a real account -- so this endpoint can't be used
    to enumerate which emails have accounts."""
    email = (payload or {}).get("email", "").strip().lower()
    generic_response = {"ok": True, "message": "If that email has an account, a reset code has been sent."}

    user = db.get_user_by_username(email)
    if not user or not user.is_active:
        return generic_response  # deliberately identical to the success path -- no enumeration signal

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=PASSWORD_SET_TOKEN_LIFETIME_HOURS)
    db.issue_password_set_token(user.id, code, expires_at)

    frontend_base = os.environ.get("ROFEHAI_PUBLIC_APP_BASE", "").rstrip("/")
    set_password_url = f"{frontend_base}/set-password.html"
    try:
        notify.send_password_setup_email(user.username, user.display_name, set_password_url, code, is_reset=True)
    except notify.EmailError:
        pass  # still return the generic response -- don't leak email-send failures to the caller either

    return generic_response


@router.post("/auth/login")
def login(payload: dict, request: Request):
    username = (payload or {}).get("username", "").strip()
    password = (payload or {}).get("password", "")
    nda_acknowledged = bool((payload or {}).get("nda_acknowledged", False))

    forwarded_for = request.headers.get("x-forwarded-for")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else None)
    user_agent = request.headers.get("user-agent")

    user = db.get_user_by_username(username)
    password_correct = bool(user and user_auth.verify_password(password, user.password_hash))
    success = bool(password_correct and user.is_active)

    db.log_gate_attempt(
        nda_acknowledged=nda_acknowledged, success=success, ip_address=client_ip, user_agent=user_agent,
        username_attempted=username, user_id=user.id if (success and user) else None,
    )

    if password_correct and user and not user.is_active:
        # Distinct from "wrong credentials" -- only shown when the
        # password was genuinely correct, so it doesn't leak account
        # existence to someone just guessing usernames.
        raise HTTPException(status_code=403, detail="Your account is pending admin approval")
    if not success:
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    token = user_auth.create_session_token(user.id)
    return {"token": token, "display_name": user.display_name, "username": user.username}


@router.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.startswith("Bearer "):
        db.delete_session(authorization[len("Bearer "):])
    return {"ok": True}


@router.post("/auth/change-password")
def change_password(payload: dict, authorization: str | None = Header(default=None)):
    """Self-service password change -- requires the CURRENT password too,
    not just a valid session token, so a stolen/leftover session alone
    can't be used to lock the real owner out. Never logs either password."""
    user = get_current_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    current_password = (payload or {}).get("current_password", "")
    new_password = (payload or {}).get("new_password", "")
    if not user_auth.verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    db.update_user_password(user.id, user_auth.hash_password(new_password))
    return {"ok": True}


def _require_domain(domain_id: str) -> None:
    if domain_id not in DOMAIN_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown health domain '{domain_id}'")


@router.get("/crew")
def list_crew():
    overrides = db.get_crew_name_overrides()
    if not overrides:
        return CREW
    return [
        member.model_copy(update={"name": overrides[member.id]}) if member.id in overrides else member
        for member in CREW
    ]


@router.put("/crew/{crew_id}/name")
def rename_crew_member(crew_id: str, payload: dict, x_api_key: str | None = Header(default=None)):
    """Relabel a crew slot's display name -- e.g. for a live pilot
    session with real reviewers. Does NOT touch the underlying synthetic
    health-signal data, which stays keyed by the original crew_id."""
    _require_admin_key(x_api_key)
    if crew_id not in {c.id for c in CREW}:
        raise HTTPException(status_code=404, detail=f"Unknown crew_id '{crew_id}'")
    display_name = (payload or {}).get("display_name", "").strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="display_name is required")
    db.set_crew_name_override(crew_id, display_name)
    return {"crew_id": crew_id, "display_name": display_name}


@router.delete("/crew/{crew_id}/name")
def reset_crew_member_name(crew_id: str, x_api_key: str | None = Header(default=None)):
    """Reverts a crew slot back to its original synthetic name."""
    _require_admin_key(x_api_key)
    db.clear_crew_name_override(crew_id)
    original = next((c for c in CREW if c.id == crew_id), None)
    return {"crew_id": crew_id, "display_name": original.name if original else None}


@router.get("/mission/meta")
def mission_meta():
    return {"mission_length_days": MISSION_LENGTH_DAYS, "name": "Mars transit (reference demo)"}


@router.get("/domains", response_model=list[DomainInfo])
def list_domains():
    """All registered health domains -- both fully active (real synthetic
    scoring) and architecture-ready placeholders. The dashboard uses this
    to render active domains as full risk cards and everything else as
    honestly-labeled 'architecture ready' badges."""
    return [DomainInfo(id=d.id, display_name=d.display_name, status=d.status) for d in all_domains()]


@router.get("/crew/{crew_id}/observations")
def get_observations(crew_id: str, mission_day: int = MISSION_LENGTH_DAYS):
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    return [o.model_dump() for o in twin.history if o.mission_day <= mission_day]


_KNOWN_CREW_IDS = {c.id for c in CREW}


@router.post("/observations", response_model=Observation)
def submit_observation(obs: Observation):
    """Ingest a new (real or manually-entered) observation for a crew member."""
    if obs.crew_id not in _KNOWN_CREW_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown crew_id '{obs.crew_id}'")
    registry.ingest(obs)
    return obs


@router.get("/crew/{crew_id}/risk/{domain_id}", response_model=RiskOutput)
def get_risk(crew_id: str, domain_id: str, mission_day: int = MISSION_LENGTH_DAYS):
    _require_domain(domain_id)
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    if not twin.history:
        raise HTTPException(status_code=404, detail="Unknown crew member")
    return score(twin, domain_id, mission_day)


@router.get("/crew/{crew_id}/risk", response_model=list[RiskOutput])
def get_all_risk(crew_id: str, mission_day: int = MISSION_LENGTH_DAYS):
    """Risk for every ACTIVE domain (architecture-ready placeholders are
    listed via /api/domains, not scored here -- there's nothing to score)."""
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    return [score(twin, d.id, mission_day) for d in active_domains()]


@router.get("/crew/{crew_id}/countermeasure/{domain_id}")
def get_countermeasure(crew_id: str, domain_id: str, mission_day: int = MISSION_LENGTH_DAYS):
    _require_domain(domain_id)
    _ensure_seeded(crew_id, mission_day)
    twin = registry.get_or_create(crew_id)
    risk = score(twin, domain_id, mission_day)
    rec = cm.recommend(risk)
    return rec.model_dump() if rec else None


@router.post("/agent/ask", response_model=AgentAskResponse)
def agent_ask(payload: AgentAskRequest):
    if payload.crew_id is not None and payload.crew_id not in _KNOWN_CREW_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown crew_id '{payload.crew_id}'")
    try:
        answer, sources, conversation_id = agent_core.run_agent(payload.crew_id, payload.question, payload.conversation_id)
    except agent_core.AgentConversationError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except agent_core.AgentRateLimitError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except agent_core.AgentError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return AgentAskResponse(
        crew_id=payload.crew_id, question=payload.question, answer=answer, sources=sources, conversation_id=conversation_id
    )


@router.get("/agent/usage")
def agent_usage(limit: int = 50):
    """Basic observability into what the agent has actually been asked
    and how it behaved -- real logging, not just 'hope it's fine'. Does
    NOT return conversation content, only call metadata."""
    calls = db.recent_agent_calls(limit)
    return [
        {
            "crew_id": c.crew_id,
            "conversation_id": c.conversation_id,
            "question_length": c.question_length,
            "tool_calls_made": c.tool_calls_made,
            "used_recall": c.used_recall,
            "created_at": c.created_at.isoformat(),
        }
        for c in calls
    ]


# --- Clinical Relay & Response (near-real-time alerting) -----------------
# See backend/app/core/monitoring.py for the detection/severity logic and
# an honest note on what's simulated (the comms link) vs. real (detection,
# packaging, timestamps).

@router.post("/monitoring/check")
def monitoring_check():
    """Run a detection sweep across the whole crew roster now. In a real
    deployment this would run on a schedule or be triggered by new
    observation data; exposed as a manual endpoint here so it can be
    demonstrated and tested directly."""
    new_alerts = monitoring.run_monitoring_sweep()
    return {"new_alerts_count": len(new_alerts), "new_alerts": new_alerts}


@router.get("/alerts")
def list_alerts(status: str | None = None, crew_id: str | None = None, limit: int = 100):
    """status: 'pending' | 'transmitted' | 'acknowledged', or omit for all."""
    alerts = db.list_alerts(status=status, crew_id=crew_id, limit=limit)
    return [monitoring._alert_to_dict(a) for a in alerts]


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: int):
    alert = db.get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Unknown alert id {alert_id}")
    return monitoring._alert_to_dict(alert)


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, payload: dict | None = None, authorization: str | None = Header(default=None)):
    """The human-in-the-loop confirmation point: a clinician marks an
    alert as reviewed. This never happens automatically. Attribution
    prefers the real authenticated user (cannot be spoofed) over a
    free-typed name in the payload, which is only a fallback for anyone
    not logged in via the real account system."""
    alert = db.get_alert(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Unknown alert id {alert_id}")
    if alert.transmitted_at is None:
        raise HTTPException(status_code=400, detail="Cannot acknowledge an alert that hasn't been transmitted yet")
    note = (payload or {}).get("note")
    user = get_current_user(authorization)
    actor_name = user.display_name if user else (payload or {}).get("actor_name")
    updated = db.acknowledge_alert(alert_id, actor_name, note)
    return monitoring._alert_to_dict(updated)


@router.get("/comms/link")
def get_comms_link():
    state = db.get_comms_link_state()
    return {"available": state.available, "changed_at": state.changed_at.isoformat()}


@router.post("/comms/link")
def set_comms_link(payload: dict):
    """Toggle the SIMULATED comms link. When set to available=true, every
    currently-queued alert transmits immediately, in priority order."""
    available = bool(payload.get("available"))
    state = db.set_comms_link_available(available)
    transmitted = []
    if available:
        transmitted_records = db.transmit_pending_alerts()
        transmitted = [monitoring._alert_to_dict(a) for a in transmitted_records]
    return {
        "available": state.available,
        "changed_at": state.changed_at.isoformat(),
        "transmitted_count": len(transmitted),
        "transmitted": transmitted,
    }


@router.get("/alerts/metrics/latency")
def alert_latency_metrics():
    return db.alert_latency_metrics()


@router.post("/countermeasures/log")
def log_countermeasure(entry: CountermeasureLogEntry, authorization: str | None = Header(default=None)):
    user = get_current_user(authorization)
    if user:
        entry.actor_name = user.display_name  # real identity overrides any free-typed name -- can't be spoofed
    db.save_countermeasure_log(entry)
    return {"logged": True}


@router.get("/countermeasures/log")
def list_countermeasure_log(crew_id: str | None = None):
    return db.load_countermeasure_log(crew_id)
