"""
Tests for real per-user authentication (backend/app/core/user_auth.py +
the /api/auth/* endpoints).

Focus areas:
- Admin user creation fails closed without ROFEHAI_ADMIN_KEY set, and
  requires the correct key when it is.
- Passwords are never returned in any response, never logged in plaintext.
- Login records a real, auditable attempt (success and failure both).
- Sessions are real and revocable (logout actually invalidates).
- Attribution on countermeasure logs and alert acknowledgments prefers
  the authenticated identity over a free-typed name (can't be spoofed).
"""

from fastapi.testclient import TestClient

from app.core import db, user_auth
from app.main import app

client = TestClient(app)


def _make_user(monkeypatch, username="reviewer1", password="a-real-password-123", display_name="Dr. Reviewer"):
    monkeypatch.setenv("ROFEHAI_ADMIN_KEY", "test-admin-key")
    try:
        r = client.post(
            "/api/auth/users",
            headers={"X-API-Key": "test-admin-key"},
            json={"username": username, "password": password, "display_name": display_name},
        )
        return r
    finally:
        monkeypatch.delenv("ROFEHAI_ADMIN_KEY", raising=False)


def test_create_user_fails_closed_without_admin_key_configured():
    """Unlike the general API key (which defaults OPEN when unset --
    convenient for local dev), account creation must fail CLOSED when
    ROFEHAI_ADMIN_KEY isn't set. 'Forgot to configure a key' must not
    silently mean 'anyone can create accounts'."""
    r = client.post("/api/auth/users", json={"username": "x", "password": "password123"})
    assert r.status_code == 503


def test_create_user_rejects_wrong_admin_key(monkeypatch):
    monkeypatch.setenv("ROFEHAI_ADMIN_KEY", "correct-key")
    try:
        r = client.post(
            "/api/auth/users",
            headers={"X-API-Key": "wrong-key"},
            json={"username": "x", "password": "password123"},
        )
        assert r.status_code == 401
    finally:
        monkeypatch.delenv("ROFEHAI_ADMIN_KEY", raising=False)


def test_create_user_succeeds_with_correct_admin_key(monkeypatch):
    r = _make_user(monkeypatch, username="newreviewer")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "newreviewer"
    # The password/hash must NEVER appear in the response.
    assert "password" not in body
    assert "password_hash" not in body


def test_create_user_rejects_short_password(monkeypatch):
    monkeypatch.setenv("ROFEHAI_ADMIN_KEY", "test-admin-key")
    try:
        r = client.post(
            "/api/auth/users",
            headers={"X-API-Key": "test-admin-key"},
            json={"username": "shortpw", "password": "abc"},
        )
        assert r.status_code == 400
    finally:
        monkeypatch.delenv("ROFEHAI_ADMIN_KEY", raising=False)


def test_create_user_rejects_duplicate_username(monkeypatch):
    _make_user(monkeypatch, username="dupeuser")
    r2 = _make_user(monkeypatch, username="dupeuser")
    assert r2.status_code == 409


def test_password_is_never_stored_in_plaintext(monkeypatch):
    _make_user(monkeypatch, username="plaintextcheck", password="my-actual-secret-password")
    stored = db.get_user_by_username("plaintextcheck")
    assert stored.password_hash != "my-actual-secret-password"
    assert "my-actual-secret-password" not in stored.password_hash


def test_login_succeeds_with_correct_credentials(monkeypatch):
    _make_user(monkeypatch, username="loginuser", password="correct-password-1")
    r = client.post(
        "/api/auth/login",
        json={"username": "loginuser", "password": "correct-password-1", "nda_acknowledged": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert "token" in body and len(body["token"]) > 20
    assert body["display_name"]


def test_login_rejects_wrong_password(monkeypatch):
    _make_user(monkeypatch, username="wrongpwuser", password="correct-password-2")
    r = client.post("/api/auth/login", json={"username": "wrongpwuser", "password": "wrong-password"})
    assert r.status_code == 401


def test_login_rejects_unknown_username():
    r = client.post("/api/auth/login", json={"username": "does-not-exist-at-all", "password": "whatever"})
    assert r.status_code == 401


def test_login_attempt_is_logged_with_username_and_outcome(monkeypatch):
    """Real audit trail requirement: both success and failure must be
    recorded, tied to the attempted username, without ever storing the
    password itself."""
    _make_user(monkeypatch, username="audituser", password="audit-password-1")
    client.post("/api/auth/login", json={"username": "audituser", "password": "audit-password-1", "nda_acknowledged": True})
    client.post("/api/auth/login", json={"username": "audituser", "password": "wrong-one"})

    attempts = db.recent_gate_attempts(limit=10)
    matching = [a for a in attempts if a.username_attempted == "audituser"]
    assert len(matching) == 2
    assert any(a.success is True for a in matching)
    assert any(a.success is False for a in matching)
    for a in matching:
        assert "audit-password-1" not in str(a.__dict__)
        assert "wrong-one" not in str(a.__dict__)


def test_resolve_session_returns_none_for_invalid_token():
    assert user_auth.resolve_session("not-a-real-token") is None


def test_resolve_session_returns_none_for_missing_token():
    assert user_auth.resolve_session(None) is None


def test_resolve_session_handles_sqlite_naive_datetime_correctly(monkeypatch):
    """Regression test for a real bug: SQLite strips timezone info from
    stored datetimes, so comparing a freshly-created session's
    (naive-after-retrieval) expires_at against a tz-aware 'now' crashed
    with TypeError. A freshly created, unexpired session must resolve
    successfully without raising."""
    _make_user(monkeypatch, username="tzcheckuser", password="tz-password-1")
    login_r = client.post("/api/auth/login", json={"username": "tzcheckuser", "password": "tz-password-1"})
    token = login_r.json()["token"]
    user = user_auth.resolve_session(token)  # must not raise TypeError
    assert user is not None
    assert user.username == "tzcheckuser"


def test_logout_actually_revokes_the_session(monkeypatch):
    """A real revocation guarantee: after logout, the same token must no
    longer resolve to a user -- not just 'until it expires'."""
    _make_user(monkeypatch, username="logoutuser", password="logout-password-1")
    login_r = client.post(
        "/api/auth/login",
        json={"username": "logoutuser", "password": "logout-password-1"},
    )
    token = login_r.json()["token"]

    assert user_auth.resolve_session(token) is not None

    client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})

    assert user_auth.resolve_session(token) is None


def test_acknowledge_alert_attributes_to_real_authenticated_user_not_spoofed_name(monkeypatch):
    """The real security property: a logged-in user's acknowledgment is
    attributed to THEM, even if the request body claims a different
    name -- the authenticated identity always wins."""
    _make_user(monkeypatch, username="ackuser", password="ack-password-1", display_name="Real Dr. Smith")
    login_r = client.post("/api/auth/login", json={"username": "ackuser", "password": "ack-password-1"})
    token = login_r.json()["token"]

    alert = db.create_alert("crew-01", "sans", "high", 0.9, 0.5, 0.8, "narrative", [], None)
    db.mark_alert_queued(alert.id)
    db.set_comms_link_available(True)
    db.transmit_pending_alerts()

    r = client.post(
        f"/api/alerts/{alert.id}/acknowledge",
        headers={"Authorization": f"Bearer {token}"},
        json={"note": "reviewed", "actor_name": "Someone Else Entirely"},  # attempted spoof
    )
    assert r.status_code == 200
    updated = db.get_alert(alert.id)
    assert updated.acknowledged_by == "Real Dr. Smith"  # real identity wins, not the spoofed payload field


def test_acknowledge_alert_falls_back_to_payload_name_when_not_logged_in():
    """Without a real session, the free-typed name is still accepted as
    a fallback -- matches the existing shared-passcode-only flow so
    nothing breaks for anyone not using the new account system yet."""
    alert = db.create_alert("crew-02", "sans", "high", 0.9, 0.5, 0.8, "narrative", [], None)
    db.mark_alert_queued(alert.id)
    db.set_comms_link_available(True)
    db.transmit_pending_alerts()

    r = client.post(f"/api/alerts/{alert.id}/acknowledge", json={"note": "reviewed", "actor_name": "Anonymous Reviewer"})
    assert r.status_code == 200
    updated = db.get_alert(alert.id)
    assert updated.acknowledged_by == "Anonymous Reviewer"


def test_log_countermeasure_attributes_to_real_user_when_logged_in(monkeypatch):
    _make_user(monkeypatch, username="cmuser", password="cm-password-1", display_name="Real Dr. Jones")
    login_r = client.post("/api/auth/login", json={"username": "cmuser", "password": "cm-password-1"})
    token = login_r.json()["token"]

    r = client.post(
        "/api/countermeasures/log",
        headers={"Authorization": f"Bearer {token}"},
        json={"crew_id": "crew-01", "countermeasure_id": "lbnp-protocol", "mission_day": 100, "actor_name": "Spoofed Name"},
    )
    assert r.status_code == 200
    logs = db.load_countermeasure_log("crew-01")
    matching = [l for l in logs if l.mission_day == 100 and l.countermeasure_id == "lbnp-protocol"]
    assert matching[-1].actor_name == "Real Dr. Jones"
