"""Verify short-lived customer sessions before constructing workflow state.

Session issuance belongs to a future login flow. No public minting endpoint exists.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException

ISSUER = "support-agent-v1"
MAX_LIFETIME_SECONDS = 3600


@dataclass(frozen=True)
class CustomerIdentity:
    customer_id: UUID
    issued_at: int
    expires_at: int


class InvalidSession(ValueError):
    """A customer session is malformed, expired, or has an invalid signature."""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not value or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for char in value
    ):
        raise InvalidSession("Invalid session")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise InvalidSession("Invalid session") from exc
    if _encode(decoded) != value:
        raise InvalidSession("Invalid session")
    return decoded


def _key(secret: str) -> bytes:
    encoded = secret.encode("utf-8")
    if len(encoded) < 32:
        raise ValueError("Customer session secret must be at least 32 bytes")
    return encoded


def _issue_identity(identity_id: UUID, secret: str, issuer: str, *, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = {
        "iss": issuer,
        "sub": str(identity_id),
        "iat": issued_at,
        "exp": issued_at + MAX_LIFETIME_SECONDS,
    }
    body = _encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _encode(hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def issue_session(customer_id: UUID, secret: str, *, now: int | None = None) -> str:
    """Internal issuer for a future authenticated login flow and test fixtures."""
    return _issue_identity(customer_id, secret, ISSUER, now=now)


def _verify_identity(
    token: str, secret: str, issuer: str, *, now: int | None = None
) -> tuple[UUID, int, int]:
    if len(token) > 2048 or token.count(".") != 1:
        raise InvalidSession("Invalid session")
    body, signature = token.split(".")
    raw_body = _decode(body)
    supplied = _decode(signature)
    expected = hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(supplied, expected):
        raise InvalidSession("Invalid session")
    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict) or set(payload) != {"iss", "sub", "iat", "exp"}:
            raise InvalidSession("Invalid session")
        if payload["iss"] != issuer or not isinstance(payload["sub"], str):
            raise InvalidSession("Invalid session")
        issued_at, expires_at = payload["iat"], payload["exp"]
        if type(issued_at) is not int or type(expires_at) is not int:
            raise InvalidSession("Invalid session")
        current = int(time.time()) if now is None else now
        if (
            issued_at > current
            or expires_at <= current
            or expires_at - issued_at > MAX_LIFETIME_SECONDS
        ):
            raise InvalidSession("Invalid session")
        identity_id = UUID(payload["sub"])
        if str(identity_id) != payload["sub"]:
            raise InvalidSession("Invalid session")
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise InvalidSession("Invalid session") from exc
    return identity_id, issued_at, expires_at


def verify_session(token: str, secret: str, *, now: int | None = None) -> CustomerIdentity:
    customer_id, issued_at, expires_at = _verify_identity(token, secret, ISSUER, now=now)
    return CustomerIdentity(customer_id, issued_at, expires_at)


def require_customer_identity(
    authorization: Annotated[str | None, Header()] = None,
) -> CustomerIdentity:
    secret = os.getenv("CUSTOMER_SESSION_SECRET")
    if not secret or len(secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="Customer authentication unavailable")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid customer session")
    try:
        return verify_session(authorization[7:], secret)
    except InvalidSession as exc:
        raise HTTPException(status_code=401, detail="Invalid customer session") from exc
