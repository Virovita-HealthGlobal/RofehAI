"""
Tests for the redesigned self-service signup flow:

signup (email + NDA only, no password) -> admin approves (emails the
REQUESTER a single-use password-setup link, never activates directly) ->
requester sets their own password via that link (which also activates
the account) -> can log in. The exact same set-password mechanism is
reused for "forgot password" on an already-active account.

Real email sending is mocked (tests must never hit the real Resend API)
-- but the actual state machine is tested for real, including the
security properties:
- A pending account has NO password hash at all, and genuinely cannot
  log in under any password, until the requester sets one themselves.
- The requester-facing password-setup email is a SEPARATE email, sent
  only on approval, never to the admin.
- Both the approve link and the password-set link are single-use.
- Forgot-password never reveals whether an email has an account
  (identical response either way).
- An invalid/malformed email is rejected before anything is created.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core import db
from app.main import app

client = TestClient(app)


def _signup(email="pending_user@example.com", display_name="Pending User", nda=True):
    with patch("app.core.notify.send_signup_approval_email") as mock_send:
        r = client.post(
            "/api/auth/signup",
            json={"email": email, "display_name": display_name, "nda_acknowledged": nda},
        )
        return r, mock_send


def _signup_and_approve(email="approve_user@example.com", display_name="Approve User"):
    """Helper: signs up and approves, returning the 6-digit code."""
    _signup(email=email, display_name=display_name)
    user = db.get_user_by_username(email)
    approval_token = user.approval_token
    with patch("app.core.notify.send_password_setup_email"):
        client.get(f"/api/auth/approve/{approval_token}")
    updated = db.get_user_by_username(email)
    return updated.password_set_token


def test_signup_rejects_invalid_email_format():
    r, mock_send = _signup(email="not-an-email-at-all")
    assert r.status_code == 400
    mock_send.assert_not_called()


def test_signup_rejects_empty_email():
    r, mock_send = _signup(email="")
    assert r.status_code == 400
    mock_send.assert_not_called()


def test_signup_accepts_real_looking_email():
    r, mock_send = _signup(email="real.looking+tag@sub.example.com")
    assert r.status_code == 200
    mock_send.assert_called_once()


def test_signup_requires_nda_acknowledgment():
    r, mock_send = _signup(email="no_nda@example.com", nda=False)
    assert r.status_code == 400
    mock_send.assert_not_called()


def test_signup_creates_inactive_account_with_no_password_at_all():
    r, mock_send = _signup(email="inactive_check@example.com")
    assert r.status_code == 200
    user = db.get_user_by_username("inactive_check@example.com")
    assert user is not None
    assert user.is_active is False
    assert user.password_hash is None
    assert user.approval_token is not None
    mock_send.assert_called_once()


def test_pending_account_cannot_log_in_under_any_password():
    _signup(email="cannot_login@example.com")
    for guess in ["", "password123", "anything-at-all", "12345678"]:
        r = client.post("/api/auth/login", json={"username": "cannot_login@example.com", "password": guess})
        assert r.status_code in (401, 403)


def test_approve_does_not_activate_directly_but_issues_a_password_link():
    _signup(email="approve_no_direct@example.com")
    user = db.get_user_by_username("approve_no_direct@example.com")
    approval_token = user.approval_token

    with patch("app.core.notify.send_password_setup_email") as mock_send:
        r = client.get(f"/api/auth/approve/{approval_token}")
        assert r.status_code == 200
        mock_send.assert_called_once()

    updated = db.get_user_by_username("approve_no_direct@example.com")
    assert updated.is_active is False
    assert updated.approval_token is None
    assert updated.password_set_token is not None


def test_approval_email_sent_to_requester_not_admin():
    _signup(email="requester_target@example.com", display_name="Target Person")
    user = db.get_user_by_username("requester_target@example.com")
    with patch("app.core.notify.send_password_setup_email") as mock_send:
        client.get(f"/api/auth/approve/{user.approval_token}")
        call_args = mock_send.call_args
        assert call_args[0][0] == "requester_target@example.com"


def test_approval_token_is_single_use():
    _signup(email="single_use@example.com")
    user = db.get_user_by_username("single_use@example.com")
    token = user.approval_token

    with patch("app.core.notify.send_password_setup_email"):
        first = client.get(f"/api/auth/approve/{token}")
        assert first.status_code == 200
        second = client.get(f"/api/auth/approve/{token}")
        assert second.status_code == 404


def test_reject_link_deletes_the_pending_account():
    _signup(email="reject_flow@example.com")
    user = db.get_user_by_username("reject_flow@example.com")
    token = user.approval_token

    r = client.get(f"/api/auth/reject/{token}")
    assert r.status_code == 200
    assert db.get_user_by_username("reject_flow@example.com") is None


def test_unknown_approval_token_returns_404():
    r = client.get("/api/auth/approve/not-a-real-token-at-all")
    assert r.status_code == 404


def test_set_password_activates_the_account_and_allows_login():
    code = _signup_and_approve(email="full_flow@example.com")
    assert code is not None
    assert len(code) == 6 and code.isdigit()

    r = client.post("/api/auth/set-password", json={"email": "full_flow@example.com", "code": code, "new_password": "my-real-password-123"})
    assert r.status_code == 200

    updated = db.get_user_by_username("full_flow@example.com")
    assert updated.is_active is True
    assert updated.password_hash is not None
    assert updated.password_set_token is None

    login_r = client.post("/api/auth/login", json={"username": "full_flow@example.com", "password": "my-real-password-123"})
    assert login_r.status_code == 200


def test_set_password_rejects_short_password():
    code = _signup_and_approve(email="short_pw_setup@example.com")
    r = client.post("/api/auth/set-password", json={"email": "short_pw_setup@example.com", "code": code, "new_password": "short"})
    assert r.status_code == 400


def test_set_password_code_is_single_use():
    code = _signup_and_approve(email="single_use_setpw@example.com")
    first = client.post("/api/auth/set-password", json={"email": "single_use_setpw@example.com", "code": code, "new_password": "first-password-123"})
    assert first.status_code == 200
    second = client.post("/api/auth/set-password", json={"email": "single_use_setpw@example.com", "code": code, "new_password": "second-password-456"})
    assert second.status_code == 404


def test_set_password_rejects_unknown_code():
    r = client.post("/api/auth/set-password", json={"email": "nobody@example.com", "code": "000000", "new_password": "whatever-12345"})
    assert r.status_code == 404


def test_set_password_rejects_correct_code_with_wrong_email():
    """The real security property of requiring both together: knowing
    someone else's code doesn't help if you don't also know which email
    it belongs to."""
    code = _signup_and_approve(email="right_email_wrong_check@example.com")
    r = client.post("/api/auth/set-password", json={"email": "wrong-email@example.com", "code": code, "new_password": "whatever-12345"})
    assert r.status_code == 404


def test_forgot_password_gives_identical_response_regardless_of_whether_email_exists():
    code = _signup_and_approve(email="forgot_pw_real@example.com")
    client.post("/api/auth/set-password", json={"email": "forgot_pw_real@example.com", "code": code, "new_password": "original-password-123"})

    with patch("app.core.notify.send_password_setup_email"):
        real_r = client.post("/api/auth/forgot-password", json={"email": "forgot_pw_real@example.com"})
    fake_r = client.post("/api/auth/forgot-password", json={"email": "definitely-does-not-exist@example.com"})

    assert real_r.status_code == fake_r.status_code == 200
    assert real_r.json() == fake_r.json()


def test_forgot_password_issues_a_working_reset_code_for_a_real_account():
    code = _signup_and_approve(email="forgot_pw_flow@example.com")
    client.post("/api/auth/set-password", json={"email": "forgot_pw_flow@example.com", "code": code, "new_password": "original-password-456"})

    with patch("app.core.notify.send_password_setup_email") as mock_send:
        client.post("/api/auth/forgot-password", json={"email": "forgot_pw_flow@example.com"})
        mock_send.assert_called_once()

    updated = db.get_user_by_username("forgot_pw_flow@example.com")
    new_code = updated.password_set_token
    assert new_code is not None
    assert new_code != code  # a genuinely new code, not the old one reused

    reset_r = client.post("/api/auth/set-password", json={"email": "forgot_pw_flow@example.com", "code": new_code, "new_password": "brand-new-password-789"})
    assert reset_r.status_code == 200

    old_login = client.post("/api/auth/login", json={"username": "forgot_pw_flow@example.com", "password": "original-password-456"})
    assert old_login.status_code == 401

    new_login = client.post("/api/auth/login", json={"username": "forgot_pw_flow@example.com", "password": "brand-new-password-789"})
    assert new_login.status_code == 200


def test_forgot_password_does_nothing_for_a_still_pending_account():
    _signup(email="still_pending@example.com")
    with patch("app.core.notify.send_password_setup_email") as mock_send:
        client.post("/api/auth/forgot-password", json={"email": "still_pending@example.com"})
        mock_send.assert_not_called()
    user = db.get_user_by_username("still_pending@example.com")
    assert user.password_set_token is None


def test_signup_email_failure_returns_502_but_account_still_exists():
    from app.core import notify as notify_module

    with patch.object(notify_module, "send_signup_approval_email", side_effect=notify_module.EmailError("SMTP down")):
        r = client.post(
            "/api/auth/signup",
            json={"email": "email_fail@example.com", "display_name": "X", "nda_acknowledged": True},
        )
        assert r.status_code == 502

    assert db.get_user_by_username("email_fail@example.com") is not None


# --- Contact form ---------------------------------------------------------

def test_contact_requires_all_fields():
    r = client.post("/api/contact", json={"name": "", "email": "", "message": ""})
    assert r.status_code == 400


def test_contact_rejects_invalid_email():
    r = client.post("/api/contact", json={"name": "A", "email": "not-an-email", "message": "hi"})
    assert r.status_code == 400


def test_contact_rejects_overlong_message():
    r = client.post("/api/contact", json={"name": "A", "email": "a@example.com", "message": "x" * 5001})
    assert r.status_code == 400


def test_contact_sends_with_real_reply_to():
    with patch("app.core.notify.send_contact_message") as mock_send:
        r = client.post(
            "/api/contact",
            json={"name": "Jane Reviewer", "email": "jane@example.com", "message": "Interested in a demo."},
        )
        assert r.status_code == 200
        mock_send.assert_called_once_with("Jane Reviewer", "jane@example.com", "Interested in a demo.")


def test_contact_email_failure_returns_502():
    from app.core import notify as notify_module

    with patch.object(notify_module, "send_contact_message", side_effect=notify_module.EmailError("down")):
        r = client.post("/api/contact", json={"name": "A", "email": "a@example.com", "message": "hi"})
        assert r.status_code == 502
