"""Development-session cookies and CSRF tokens. This is not enterprise SSO.

A session is `role.nonce.signature`. Sessions issued before roles existed
(`nonce.signature`) are still accepted as analyst sessions.
"""

import hashlib
import hmac
import secrets

ANALYST = "analyst"
APPROVER = "approver"
ROLES = (ANALYST, APPROVER)


def _sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def issue_session(secret: str, role: str = ANALYST) -> str:
    if role not in ROLES:
        raise ValueError("Unknown role.")
    nonce = secrets.token_hex(16)
    return f"{role}.{nonce}.{_sign(secret, f'{role}:{nonce}')}"


def session_role(secret: str, cookie: str | None) -> str | None:
    if not cookie or "." not in cookie:
        return None
    parts = cookie.split(".")
    if len(parts) == 3 and parts[0] in ROLES:
        role, nonce, signature = parts
        return role if hmac.compare_digest(_sign(secret, f"{role}:{nonce}"), signature) else None
    if len(parts) == 2:
        nonce, signature = parts
        return ANALYST if hmac.compare_digest(_sign(secret, f"ok:{nonce}"), signature) else None
    return None


def session_valid(secret: str, cookie: str | None) -> bool:
    return session_role(secret, cookie) is not None


def csrf_token(secret: str, session: str) -> str:
    return hmac.new(secret.encode(), f"csrf:{session}".encode(), hashlib.sha256).hexdigest()


def csrf_valid(secret: str, session: str | None, supplied: str | None) -> bool:
    if not session or not supplied or not session_valid(secret, session):
        return False
    expected = csrf_token(secret, session)
    return hmac.compare_digest(expected, supplied)


def token_matches(expected: str, supplied: str) -> bool:
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected, supplied)
