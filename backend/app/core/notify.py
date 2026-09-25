"""
Email notifications via Resend (https://resend.com), for the self-service
signup approval flow.

Deliberately does NOT email any password -- ever. The notification
contains only the requester's username, display name, and NDA-
acknowledgment timestamp, plus one-click Approve/Reject links. See
db.User's docstring and the signup endpoint in routes.py.
"""

from __future__ import annotations

import os

import httpx

RESEND_API_KEY_ENV_VAR = "RESEND_API_KEY"
ADMIN_NOTIFY_EMAIL_ENV_VAR = "ROFEHAI_ADMIN_NOTIFY_EMAIL"  # comma-separated for multiple recipients
RESEND_FROM_ENV_VAR = "ROFEHAI_NOTIFY_FROM_EMAIL"  # must be a verified sending domain in Resend


class EmailError(Exception):
    pass


def send_signup_approval_email(
    username: str, display_name: str, nda_acknowledged_at, approve_url: str, reject_url: str
) -> None:
    api_key = os.environ.get(RESEND_API_KEY_ENV_VAR)
    recipients_raw = os.environ.get(ADMIN_NOTIFY_EMAIL_ENV_VAR, "")
    from_address = os.environ.get(RESEND_FROM_ENV_VAR)

    if not api_key or not recipients_raw or not from_address:
        raise EmailError(
            f"{RESEND_API_KEY_ENV_VAR}, {ADMIN_NOTIFY_EMAIL_ENV_VAR}, and {RESEND_FROM_ENV_VAR} "
            "must all be set to send signup notifications."
        )
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    html_body = f"""
    <p>A new RofehAI dashboard account is requesting access.</p>
    <ul>
      <li><strong>Username:</strong> {username}</li>
      <li><strong>Display name:</strong> {display_name}</li>
      <li><strong>NDA acknowledged at:</strong> {nda_acknowledged_at}</li>
    </ul>
    <p>
      <a href="{approve_url}" style="background:#2563eb;color:#fff;padding:10px 18px;
        border-radius:6px;text-decoration:none;margin-right:10px;">Approve</a>
      <a href="{reject_url}" style="background:#dc2626;color:#fff;padding:10px 18px;
        border-radius:6px;text-decoration:none;">Reject</a>
    </p>
    <p style="color:#888;font-size:12px;">This request will not be able to log in until approved.
    No password was included in this email, by design.</p>
    """

    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "from": from_address,
            "to": recipients,
            "subject": f"RofehAI access request: {username}",
            "html": html_body,
        },
        timeout=10.0,
    )
    if response.status_code >= 400:
        raise EmailError(f"Resend API returned {response.status_code}: {response.text}")


def send_password_setup_email(email: str, display_name: str, set_password_url: str, is_reset: bool = False) -> None:
    """Sent to the REQUESTER, never the admin -- the one email in this
    whole flow that contains a way to actually get into the account.
    Never contains a password itself, only a single-use link the
    requester uses to choose their own."""
    api_key = os.environ.get(RESEND_API_KEY_ENV_VAR)
    from_address = os.environ.get(RESEND_FROM_ENV_VAR)
    if not api_key or not from_address:
        raise EmailError(f"{RESEND_API_KEY_ENV_VAR} and {RESEND_FROM_ENV_VAR} must be set to send this email.")

    verb = "Reset your password" if is_reset else "Create your password"
    intro = (
        "Use the link below to reset your RofehAI password."
        if is_reset
        else "Your RofehAI access request has been approved. Use the link below to create your password and sign in."
    )

    html_body = f"""
    <p>Hi {display_name},</p>
    <p>{intro}</p>
    <p>
      <a href="{set_password_url}" style="background:#2563eb;color:#fff;padding:10px 18px;
        border-radius:6px;text-decoration:none;">{verb}</a>
    </p>
    <p style="color:#888;font-size:12px;">This link expires in 48 hours and can only be used once.
    If you didn't request this, you can safely ignore this email.</p>
    """

    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "from": from_address,
            "to": [email],
            "subject": "Reset your RofehAI password" if is_reset else "You're approved — create your RofehAI password",
            "html": html_body,
        },
        timeout=10.0,
    )
    if response.status_code >= 400:
        raise EmailError(f"Resend API returned {response.status_code}: {response.text}")


def send_contact_message(name: str, email: str, message: str) -> None:
    """The general-inquiry contact form on the marketing page -- reuses
    the same admin recipient list as signup approvals. No account is
    created; this is just a message forward."""
    api_key = os.environ.get(RESEND_API_KEY_ENV_VAR)
    recipients_raw = os.environ.get(ADMIN_NOTIFY_EMAIL_ENV_VAR, "")
    from_address = os.environ.get(RESEND_FROM_ENV_VAR)
    if not api_key or not recipients_raw or not from_address:
        raise EmailError(
            f"{RESEND_API_KEY_ENV_VAR}, {ADMIN_NOTIFY_EMAIL_ENV_VAR}, and {RESEND_FROM_ENV_VAR} "
            "must all be set to send contact messages."
        )
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    html_body = f"""
    <p>New message from the RofehAI contact form.</p>
    <ul>
      <li><strong>Name:</strong> {name}</li>
      <li><strong>Email:</strong> {email}</li>
    </ul>
    <p style="white-space:pre-wrap;">{message}</p>
    """

    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "from": from_address,
            "to": recipients,
            "reply_to": email,
            "subject": f"RofehAI contact form: {name}",
            "html": html_body,
        },
        timeout=10.0,
    )
    if response.status_code >= 400:
        raise EmailError(f"Resend API returned {response.status_code}: {response.text}")
