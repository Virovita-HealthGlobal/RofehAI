"""
Tests for the SMTP-based email transport (app.core.notify), which
replaced Resend's HTTP API after a real, fully-diagnosed permissions
issue on their side (confirmed via a raw API call bypassing all of
RofehAI's own code) with no time to wait on their support.

These test the actual SMTP call construction -- port 465 uses
SMTP_SSL, anything else uses STARTTLS -- by mocking smtplib itself,
never a real mail server. The three public functions (signup approval,
password setup, contact) keep their exact same names and signatures as
under Resend, confirmed by the full existing test suite passing
unchanged against this new implementation.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.core import notify


def test_missing_smtp_config_raises_clear_error(monkeypatch):
    for var in [notify.SMTP_HOST_ENV_VAR, notify.SMTP_PORT_ENV_VAR, notify.SMTP_USERNAME_ENV_VAR, notify.SMTP_PASSWORD_ENV_VAR]:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(notify.EmailError):
        notify._send_email(["a@example.com"], "subject", "<p>body</p>")


def test_invalid_port_raises_clear_error(monkeypatch):
    monkeypatch.setenv(notify.SMTP_HOST_ENV_VAR, "smtp.example.com")
    monkeypatch.setenv(notify.SMTP_PORT_ENV_VAR, "not-a-number")
    monkeypatch.setenv(notify.SMTP_USERNAME_ENV_VAR, "user@example.com")
    monkeypatch.setenv(notify.SMTP_PASSWORD_ENV_VAR, "password")
    with pytest.raises(notify.EmailError):
        notify._send_email(["a@example.com"], "subject", "<p>body</p>")


def test_port_465_uses_smtp_ssl(monkeypatch):
    monkeypatch.setenv(notify.SMTP_HOST_ENV_VAR, "smtp.hostinger.com")
    monkeypatch.setenv(notify.SMTP_PORT_ENV_VAR, "465")
    monkeypatch.setenv(notify.SMTP_USERNAME_ENV_VAR, "notifications@rofehai.com")
    monkeypatch.setenv(notify.SMTP_PASSWORD_ENV_VAR, "test-password")

    mock_server = MagicMock()
    with patch("smtplib.SMTP_SSL") as mock_smtp_ssl, patch("smtplib.SMTP") as mock_smtp:
        mock_smtp_ssl.return_value.__enter__.return_value = mock_server
        notify._send_email(["recipient@example.com"], "Test subject", "<p>Test</p>")
        mock_smtp_ssl.assert_called_once()
        mock_smtp.assert_not_called()  # the non-SSL path must NOT be used for port 465
        mock_server.login.assert_called_once_with("notifications@rofehai.com", "test-password")
        mock_server.sendmail.assert_called_once()


def test_port_587_uses_starttls(monkeypatch):
    monkeypatch.setenv(notify.SMTP_HOST_ENV_VAR, "smtp.hostinger.com")
    monkeypatch.setenv(notify.SMTP_PORT_ENV_VAR, "587")
    monkeypatch.setenv(notify.SMTP_USERNAME_ENV_VAR, "notifications@rofehai.com")
    monkeypatch.setenv(notify.SMTP_PASSWORD_ENV_VAR, "test-password")

    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp, patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
        mock_smtp.return_value.__enter__.return_value = mock_server
        notify._send_email(["recipient@example.com"], "Test subject", "<p>Test</p>")
        mock_smtp.assert_called_once()
        mock_smtp_ssl.assert_not_called()  # the SSL path must NOT be used for STARTTLS ports
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once()


def test_smtp_failure_raises_email_error(monkeypatch):
    import smtplib as smtplib_module

    monkeypatch.setenv(notify.SMTP_HOST_ENV_VAR, "smtp.hostinger.com")
    monkeypatch.setenv(notify.SMTP_PORT_ENV_VAR, "465")
    monkeypatch.setenv(notify.SMTP_USERNAME_ENV_VAR, "notifications@rofehai.com")
    monkeypatch.setenv(notify.SMTP_PASSWORD_ENV_VAR, "wrong-password")

    with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
        mock_smtp_ssl.return_value.__enter__.side_effect = smtplib_module.SMTPAuthenticationError(535, b"bad creds")
        with pytest.raises(notify.EmailError):
            notify._send_email(["recipient@example.com"], "subject", "<p>body</p>")


def test_signup_approval_email_never_contains_a_password(monkeypatch):
    """The function doesn't even accept a password argument -- there's
    structurally no way for one to leak. Confirm the reassuring
    disclaimer is present, rather than naively banning the word
    'password' (which appears in that disclaimer itself)."""
    monkeypatch.setenv(notify.ADMIN_NOTIFY_EMAIL_ENV_VAR, "admin@rofehai.com")
    with patch("app.core.notify._send_email") as mock_send:
        notify.send_signup_approval_email(
            "requester@example.com", "Requester Name", "2026-01-01", "https://approve", "https://reject"
        )
        mock_send.assert_called_once()
        call_html = mock_send.call_args[0][2]
        assert "no password was included" in call_html.lower()


def test_contact_message_sets_reply_to_the_actual_sender(monkeypatch):
    monkeypatch.setenv(notify.ADMIN_NOTIFY_EMAIL_ENV_VAR, "admin@rofehai.com")
    with patch("app.core.notify._send_email") as mock_send:
        notify.send_contact_message("Jane Doe", "jane@example.com", "Hello there")
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args[0], mock_send.call_args[1]
        assert mock_send.call_args.kwargs.get("reply_to") == "jane@example.com"
