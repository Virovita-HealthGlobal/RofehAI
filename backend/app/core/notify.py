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
