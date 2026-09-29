"""
Free/personal email-provider denylist for self-service signup.

RofehAI's signup flow (POST /auth/signup) is documented as collecting
ONLY a professional email address -- see routes.py's signup() docstring.
Before this module existed, that promise wasn't enforced: any
syntactically valid email (personal or business) was accepted, because
the only check was a generic regex format match. This module makes the
check real.

FREE_EMAIL_DOMAINS is a plain, editable set of consumer/free email
provider domains, kept in ONE place so the policy is auditable: anyone
reviewing signup behavior can read this list directly instead of
reverse-engineering scattered logic. It is intentionally NOT exhaustive
(new free providers appear regularly, and country-specific ones exist
beyond what's listed here) -- it blocks the most common ones. Extend it
here, not elsewhere, and add a test case in test_signup.py alongside
any addition.
"""

from __future__ import annotations

FREE_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.co.in", "ymail.com", "rocketmail.com",
    "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com",
    "protonmail.com", "proton.me", "pm.me",
    "gmx.com", "gmx.net",
    "zoho.com",
    "mail.com",
    "yandex.com", "yandex.ru",
    "comcast.net", "verizon.net", "att.net", "sbcglobal.net",
    "qq.com", "163.com", "126.com",
    "inbox.com", "fastmail.com",
})


def domain_of(email: str) -> str:
    """Lowercased domain portion of an email address. Callers should
    validate the email's format first -- this assumes at least one '@'."""
    return email.rsplit("@", 1)[-1].strip().lower()


def is_free_email_domain(email: str) -> bool:
    """True if the email's domain is a known free/personal provider."""
    return domain_of(email) in FREE_EMAIL_DOMAINS
