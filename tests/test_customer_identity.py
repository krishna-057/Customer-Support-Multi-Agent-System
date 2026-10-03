"""Customer identity is signed, short lived, and sourced from server configuration."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from support_system.customer_identity import (
    MAX_LIFETIME_SECONDS,
    InvalidSession,
    issue_session,
    require_customer_identity,
    verify_session,
)

SECRET = "fixture-customer-session-secret-32-bytes-minimum"


def test_signed_customer_identity_round_trip():
    customer_id = uuid4()
    token = issue_session(customer_id, SECRET, now=1000)
    claims = verify_session(token, SECRET, now=1001)
    assert claims.customer_id == customer_id
    assert claims.issued_at == 1000
    assert claims.expires_at == 1000 + MAX_LIFETIME_SECONDS


@pytest.mark.parametrize("token_change", ["suffix", "signature", "payload"])
def test_tampered_sessions_fail(token_change):
    token = issue_session(uuid4(), SECRET, now=1000)
    body, signature = token.split(".")
    changed = {
        "suffix": token + "x",
        "signature": body + "." + ("A" if signature[0] != "A" else "B") + signature[1:],
        "payload": ("A" if body[0] != "A" else "B") + body[1:] + "." + signature,
    }[token_change]
    with pytest.raises(InvalidSession):
        verify_session(changed, SECRET, now=1001)


def test_expired_or_future_sessions_fail():
    token = issue_session(uuid4(), SECRET, now=1000)
    with pytest.raises(InvalidSession):
        verify_session(token, SECRET, now=999)
    with pytest.raises(InvalidSession):
        verify_session(token, SECRET, now=1000 + MAX_LIFETIME_SECONDS)


def test_missing_or_short_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("CUSTOMER_SESSION_SECRET", raising=False)
    with pytest.raises(HTTPException) as missing:
        require_customer_identity("Bearer any")
    assert missing.value.status_code == 503
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", "too-short")
    with pytest.raises(HTTPException) as short:
        require_customer_identity("Bearer any")
    assert short.value.status_code == 503


def test_dependency_rejects_invalid_authorization(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    with pytest.raises(HTTPException) as missing:
        require_customer_identity(None)
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as forged:
        require_customer_identity("Bearer invalid")
    assert forged.value.status_code == 401


def test_dependency_returns_only_signed_customer_claims(monkeypatch):
    customer_id = uuid4()
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    token = issue_session(customer_id, SECRET)
    claims = require_customer_identity(f"Bearer {token}")
    assert claims.customer_id == customer_id
