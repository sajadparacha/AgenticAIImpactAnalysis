"""Development-session cookies and CSRF tokens. This is not enterprise SSO."""

import hashlib
import hmac
import secrets


def issue_session(secret: str) -> str:
    nonce = secrets.token_hex(16)
    signature = hmac.new(secret.encode(), f"ok:{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{signature}"


def session_valid(secret: str, cookie: str | None) -> bool:
    if not cookie or "." not in cookie:
        return False
    nonce, signature = cookie.rsplit(".", 1)
    expected = hmac.new(secret.encode(), f"ok:{nonce}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


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
