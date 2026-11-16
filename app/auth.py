from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

from app.config import Settings

SESSION_COOKIE_NAME = "keitaro_wrapper_session"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def create_session_token(settings: Settings, *, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else int(now)
    payload = _encode(
        f"{settings.app_username}\n{issued_at}\n{secrets.token_urlsafe(18)}".encode("utf-8")
    )
    signature = hmac.new(
        settings.app_session_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{payload}.{_encode(signature)}"


def is_valid_session(settings: Settings, token: str | None, *, now: int | None = None) -> bool:
    if not settings.app_auth_configured or not token:
        return False
    try:
        payload, supplied_signature = token.split(".", 1)
        expected_signature = _encode(
            hmac.new(
                settings.app_session_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
            ).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False
        username, issued_at_raw, nonce = _decode(payload).decode("utf-8").split("\n", 2)
        issued_at = int(issued_at_raw)
        current_time = int(time.time()) if now is None else int(now)
        return (
            username == settings.app_username
            and bool(nonce)
            and issued_at > 0
            and issued_at <= current_time + 60
            and current_time - issued_at <= settings.app_session_ttl_seconds
        )
    except (ValueError, UnicodeDecodeError, TypeError, binascii.Error):
        return False


def credentials_match(settings: Settings, username: str, password: str) -> bool:
    if not settings.app_auth_configured:
        return False
    username_matches = hmac.compare_digest(username.strip().encode("utf-8"), settings.app_username.encode("utf-8"))
    password_matches = hmac.compare_digest(password.encode("utf-8"), settings.app_password.encode("utf-8"))
    return username_matches and password_matches


def origin_matches_host(origin: str, host: str) -> bool:
    try:
        parsed = urlsplit(origin)
        request_host = urlsplit(f"//{host}")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or not request_host.hostname
        ):
            return False
        origin_host = parsed.hostname.lower()
        if origin_host != request_host.hostname.lower():
            return False
        default_port = 443 if parsed.scheme == "https" else 80
        return (parsed.port or default_port) == (request_host.port or default_port)
    except ValueError:
        return False
