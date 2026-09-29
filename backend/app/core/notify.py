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


def _html_to_text(html: str) -> str:
    """Best-effort plain-text version of one of our own HTML email
    bodies, for the required multipart/alternative text/plain part.
    Not a general HTML parser -- only needs to handle the small set of
    tags actually used in the templates below."""
    import re

    text = html
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r'(?i)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
    message["From"] = f"RofehAI <{username}>"
    message["To"] = ", ".join(to_addresses)
    if reply_to:
        message["Reply-To"] = reply_to
    # Attach plain-text FIRST, then HTML -- multipart/alternative order is
    # least-preferred-first. An HTML-only multipart/alternative message is
    # a well-known spam-filter trigger even when SPF/DKIM/DMARC all pass
    # (confirmed via a live X-Spam: Yes flag on a real Hostinger delivery
    # with fully-passing auth) -- never attach HTML-only again.
    message.attach(MIMEText(_html_to_text(html_body), "plain"))
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
    <div style="font-family:Arial,Helvetica,sans-serif; max-width:480px; margin:0 auto; padding:32px 24px; background:#0b1220; border-radius:12px; color:#e6e9f0;">
      <div style="text-align:center; margin-bottom:24px;">
        <div style="font-size:20px; font-weight:700; color:#ffffff; letter-spacing:0.5px;">RofehAI</div>
        <div style="font-size:12px; color:#8891a8; margin-top:2px;">your physician, everywhere</div>
      </div>
      <p style="font-size:15px; color:#e6e9f0;">Hi {display_name},</p>
      <p style="font-size:15px; color:#e6e9f0;">{intro}</p>
      <div style="text-align:center; margin:28px 0;">
        <span style="display:inline-block; font-size:32px; font-weight:700; letter-spacing:6px; font-family:'Courier New',monospace; background:#141d33; color:#ffffff; padding:16px 24px; border-radius:8px; border:1px solid #2a3a5c;">{code}</span>
      </div>
      <p style="text-align:center; margin:24px 0;">
        <a href="{set_password_url}" style="display:inline-block; background:#2563eb; color:#ffffff; font-size:14px; font-weight:600; text-decoration:none; padding:12px 28px; border-radius:6px;">Enter code &amp; set password</a>
      </p>
      <p style="font-size:12px; color:#8891a8; text-align:center; word-break:break-all;">Or open: <a href="{set_password_url}" style="color:#6f9bff;">{set_password_url}</a></p>
      <hr style="border:none; border-top:1px solid #232d45; margin:28px 0 16px;">
      <p style="font-size:12px; color:#6b7387;">This code expires in 48 hours and can only be used once. If you didn't request this, you can safely ignore this email.</p>
      <p style="font-size:11px; color:#4a5068; text-align:center; margin-top:20px;">RofehAI reference implementation &middot; synthetic demo data &middot; not a medical device</p>
    </div>
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
