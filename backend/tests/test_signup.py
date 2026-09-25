"""
Tests for self-service signup (POST /api/auth/signup) and the one-click
email approval flow (GET /api/auth/approve/{token}, /reject/{token}).

Real email sending is mocked (tests must never hit the real Resend API)
-- but the actual state machine (pending -> approved/rejected -> login
gating) is tested for real, including the security properties:
- A pending account genuinely cannot log in, even with the right password.
- No password is ever included in what would be emailed.
- An approval token is single-use -- can't be replayed to re-approve or
  flip a rejected/approved account back and forth.
- NDA acknowledgment is required at signup, not optional.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core import db
from app.main import app

client = TestClient(app)


def _signup(username="pending_user", password="a-real-password-1", display_name="Pending User", nda=True):
    with patch("app.core.notify.send_signup_approval_email") as mock_send:
        r = client.post(
            "/api/auth/signup",
            json={"username": username, "password": password, "display_name": display_name, "nda_acknowledged": nda},
        )
        return r, mock_send


def test_signup_requires_nda_acknowledgment():
    r, mock_send = _signup(username="no_nda_user", nda=False)
    assert r.status_code == 400
    mock_send.assert_not_called()


def test_signup_rejects_short_password():
    r, mock_send = _signup(username="shortpw_signup", password="abc")
    assert r.status_code == 400
    mock_send.assert_not_called()


def test_signup_creates_inactive_account():
    r, mock_send = _signup(username="inactive_check_user")
    assert r.status_code == 200
    user = db.get_user_by_username("inactive_check_user")
    assert user is not None
    assert user.is_active is False  # cannot log in yet
    assert user.approval_token is not None
    mock_send.assert_called_once()


def test_signup_email_never_contains_password():
    """The core safety property this whole design exists for: the
    admin-facing notification must never contain the password, hashed
    or otherwise."""
    r, mock_send = _signup(username="no_password_leak_user", password="my-actual-secret-password-123")
    assert r.status_code == 200
    call_args = mock_send.call_args
    # send_signup_approval_email(username, display_name, nda_acknowledged_at, approve_url, reject_url)
    all_args_str = str(call_args)
    assert "my-actual-secret-password-123" not in all_args_str


def test_pending_account_cannot_log_in_even_with_correct_password():
    _signup(username="cannot_login_yet_user", password="correct-password-here-1")
    r = client.post("/api/auth/login", json={"username": "cannot_login_yet_user", "password": "correct-password-here-1"})
    assert r.status_code == 403
    assert "pending" in r.json()["detail"].lower()


def test_pending_account_failed_login_still_shows_generic_error_for_wrong_password():
    """Even though a CORRECT password on a pending account gets a
    distinct 'pending approval' message, a WRONG password should still
    show the generic error -- must not leak whether the account exists
    or its approval state to someone just guessing."""
    _signup(username="pending_wrongpw_user", password="the-real-password-1")
    r = client.post("/api/auth/login", json={"username": "pending_wrongpw_user", "password": "totally-wrong"})
    assert r.status_code == 401
    assert "pending" not in r.json()["detail"].lower()


def test_approve_link_activates_the_account():
    r, mock_send = _signup(username="approve_flow_user", password="approve-me-password-1")
    user = db.get_user_by_username("approve_flow_user")
    token = user.approval_token

    approve_r = client.get(f"/api/auth/approve/{token}")
    assert approve_r.status_code == 200
    assert "approved" in approve_r.text.lower()

    updated_user = db.get_user_by_username("approve_flow_user")
    assert updated_user.is_active is True
    assert updated_user.approval_token is None  # single-use: cleared after approval

    login_r = client.post("/api/auth/login", json={"username": "approve_flow_user", "password": "approve-me-password-1"})
    assert login_r.status_code == 200


def test_approval_token_is_single_use():
    """Regression-style guarantee: clicking Approve twice (e.g. an email
    client prefetching the link, or the admin double-clicking) must not
    do anything the second time -- not re-approve, not error in a way
    that implies re-processing happened."""
    _signup(username="single_use_token_user", password="single-use-password-1")
    user = db.get_user_by_username("single_use_token_user")
    token = user.approval_token

    first = client.get(f"/api/auth/approve/{token}")
    assert first.status_code == 200

    second = client.get(f"/api/auth/approve/{token}")
    assert second.status_code == 404  # token already consumed, can't be replayed


def test_reject_link_deletes_the_pending_account():
    _signup(username="reject_flow_user", password="reject-me-password-1")
    user = db.get_user_by_username("reject_flow_user")
    token = user.approval_token

    reject_r = client.get(f"/api/auth/reject/{token}")
    assert reject_r.status_code == 200
    assert "rejected" in reject_r.text.lower()

    assert db.get_user_by_username("reject_flow_user") is None

    login_r = client.post("/api/auth/login", json={"username": "reject_flow_user", "password": "reject-me-password-1"})
    assert login_r.status_code == 401


def test_unknown_approval_token_returns_404():
    r = client.get("/api/auth/approve/not-a-real-token-at-all")
    assert r.status_code == 404


def test_signup_email_failure_returns_502_but_account_still_exists():
    """If Resend is down or misconfigured, the signup itself shouldn't
    silently succeed as if nothing went wrong -- but the pending account
    should still exist so an admin can find and approve it manually."""
    from app.core import notify as notify_module

    with patch.object(notify_module, "send_signup_approval_email", side_effect=notify_module.EmailError("SMTP down")):
        r = client.post(
            "/api/auth/signup",
            json={"username": "email_fail_user", "password": "email-fail-password-1", "display_name": "X", "nda_acknowledged": True},
        )
        assert r.status_code == 502

    assert db.get_user_by_username("email_fail_user") is not None
