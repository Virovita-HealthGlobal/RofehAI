"""
Real per-user authentication.

Replaces the shared-passcode gate's "anyone who knows the secret is
indistinguishable from anyone else" model with actual accounts: bcrypt-
hashed passwords, server-side sessions that can be immediately revoked,
and NDA acknowledgment recorded against a real user identity rather than
just an IP/user-agent fingerprint.

NO PUBLIC SIGNUP. Accounts are created by an admin via
POST /api/auth/users, protected by the existing ROFEHAI_API_KEY
mechanism (see app.core.auth) -- this matches the product's own stated
model ("contact the RofehAI team if you need access") rather than
inventing a separate admin-auth system.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import bcrypt

from app.core import db

SESSION_LIFETIME_HOURS = 24 * 7  # one week -- reasonable for a reviewer demo, not indefinite


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plaintext: str, password_hash: str | None) -> bool:
    if password_hash is None:
        return False  # pending account with no password set yet -- never crash, just fail
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed hash (shouldn't happen in practice) -- fail closed, not open.
        return False


def _now_naive_utc() -> datetime:
    """SQLite strips timezone info from stored datetimes on retrieval,
    so a tz-aware 'now' can't be compared directly against a value read
    back from the DB (raises TypeError). Normalize both sides to naive
    UTC for comparison -- this is safe because every datetime in this
    module is UTC by convention, never local time."""
    return datetime.now(UTC).replace(tzinfo=None)


def create_session_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)  # 256 bits of entropy -- not guessable
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=SESSION_LIFETIME_HOURS)
    db.create_session(token, user_id, expires_at)
    return token


def resolve_session(token: str | None):
    """Returns the User for a valid, unexpired session token, or None.
    An expired session is treated as invalid (not auto-deleted here to
    keep this function read-only/side-effect-free; expired rows are
    harmless clutter, not a security issue, since they're rejected on
    every check regardless)."""
    if not token:
        return None
    session = db.get_session(token)
    if session is None:
        return None
    if session.expires_at < _now_naive_utc():
        return None
    return db.get_user_by_id(session.user_id)
