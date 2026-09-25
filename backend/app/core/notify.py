"""
Email notifications via plain SMTP (e.g. your existing Hostinger
mailbox), for the self-service signup approval flow, password resets,
and the marketing page's contact form.

Switched from Resend's HTTP API to SMTP after Resend's account-side
permissions rejected every send attempt (confirmed via a raw API call
bypassing all of RofehAI's own code -- genuinely their side, not a
config mistake here) and there wasn't time to wait on their support.
SMTP through an already-working mailbox sidesteps that entirely. Resend
could be reinstated later by swapping this module's internals back;
every OTHER function's signature is unchanged, so nothing calling into
this module would need to change either way.

Deliberately does NOT email any password -- ever. The signup
notification contains only the requester's email, display name, and
NDA-acknowledgment timestamp, plus one-click Approve/Reject links. See
db.User's docstring and the signup endpoint in routes.py.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST_ENV_VAR = "ROFEHAI_SMTP_HOST"
SMTP_PORT_ENV_VAR = "ROFEHAI_SMTP_PORT"
SMTP_USERNAME_ENV_VAR = "ROFEHAI_SMTP_USERNAME"
SMTP_PASSWORD_ENV_VAR = "ROFEHAI_SMTP_PASSWORD"
ADMIN_NOTIFY_EMAIL_ENV_VAR = "ROFEHAI_ADMIN_NOTIFY_EMAIL"  # comma-separated for multiple recipients


class EmailError(Exception):
    pass


def _send_email(to_addresses: list[str], subject: str, html_body: str, reply_to: str | None = None) -> None:
    """The one place that actually talks to the SMTP server. Every
    public function below builds its own HTML content and calls this."""
    host = os.environ.get(SMTP_HOST_ENV_VAR)
    port_raw = os.environ.get(SMTP_PORT_ENV_VAR)
    username = os.environ.get(SMTP_USERNAME_ENV_VAR)
    password = os.environ.get(SMTP_PASSWORD_ENV_VAR)

    if not host or not port_raw or not username or not password:
        raise EmailError(
            f"{SMTP_HOST_ENV_VAR}, {SMTP_PORT_ENV_VAR}, {SMTP_USERNAME_ENV_VAR}, and "
            f"{SMTP_PASSWORD_ENV_VAR} must all be set to send email."
        )
    try:
        port = int(port_raw)
    except ValueError:
        raise EmailError(f"{SMTP_PORT_ENV_VAR} must be a number, got '{port_raw}'")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = username
    message["To"] = ", ".join(to_addresses)
    if reply_to:
        message["Reply-To"] = reply_to
    message.attach(MIMEText(html_body, "html"))

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(username, password)
                server.sendmail(username, to_addresses, message.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.starttls()
                server.login(username, password)
                server.sendmail(username, to_addresses, message.as_string())
    except (smtplib.SMTPException, OSError) as e:
        raise EmailError(f"SMTP send failed: {e}")


def send_signup_approval_email(
    email: str, display_name: str, nda_acknowledged_at, approve_url: str, reject_url: str
) -> None:
    recipients_raw = os.environ.get(ADMIN_NOTIFY_EMAIL_ENV_VAR, "")
    if not recipients_raw:
        raise EmailError(f"{ADMIN_NOTIFY_EMAIL_ENV_VAR} must be set to send signup notifications.")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    html_body = f"""
    <p>A new RofehAI dashboard account is requesting access.</p>
    <ul>
      <li><strong>Email:</strong> {email}</li>
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
    _send_email(recipients, f"RofehAI access request: {email}", html_body)


def send_password_setup_email(email: str, display_name: str, set_password_url: str, code: str, is_reset: bool = False) -> None:
    """Sent to the REQUESTER, never the admin -- the one email in this
    whole flow that contains a way to actually get into the account.
    Never contains a password itself, only a single-use 6-digit code
    the requester enters alongside their email to choose their own."""
    intro = (
        "Use the code below to reset your RofehAI password."
        if is_reset
        else "Your RofehAI access request has been approved. Use the code below to create your password and sign in."
    )
    html_body = f"""
    <p>Hi {display_name},</p>
    <p>{intro}</p>
    <p style="font-size:32px;font-weight:700;letter-spacing:6px;font-family:monospace;
      background:#f0f0f0;padding:16px 20px;border-radius:8px;display:inline-block;">{code}</p>
    <p>Enter this code along with your email at:
      <a href="{set_password_url}">{set_password_url}</a></p>
    <p style="color:#888;font-size:12px;">This code expires in 48 hours and can only be used once.
    If you didn't request this, you can safely ignore this email.</p>
    """
    subject = "Reset your RofehAI password" if is_reset else "You're approved — create your RofehAI password"
    _send_email([email], subject, html_body)


def send_contact_message(name: str, email: str, message: str) -> None:
    """The general-inquiry contact form on the marketing page -- reuses
    the same admin recipient list as signup approvals. No account is
    created; this is just a message forward."""
    recipients_raw = os.environ.get(ADMIN_NOTIFY_EMAIL_ENV_VAR, "")
    if not recipients_raw:
        raise EmailError(f"{ADMIN_NOTIFY_EMAIL_ENV_VAR} must be set to send contact messages.")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]

    html_body = f"""
    <p>New message from the RofehAI contact form.</p>
    <ul>
      <li><strong>Name:</strong> {name}</li>
      <li><strong>Email:</strong> {email}</li>
    </ul>
    <p style="white-space:pre-wrap;">{message}</p>
    """
    _send_email(recipients, f"RofehAI contact form: {name}", html_body, reply_to=email)
