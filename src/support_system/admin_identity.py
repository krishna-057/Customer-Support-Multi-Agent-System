"""Verify short-lived administrator sessions for CRM decisions."""

import os
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException

from support_system.customer_identity import (
    InvalidSession,
    _issue_identity,
    _verify_identity,
)

ADMIN_ISSUER = "support-admin-v1"


@dataclass(frozen=True)
class AdminIdentity:
    admin_id: UUID


def issue_admin_session(admin_id: UUID, secret: str, *, now: int | None = None) -> str:
    """Internal issuer for a future authenticated admin login flow and test fixtures."""
    return _issue_identity(admin_id, secret, ADMIN_ISSUER, now=now)


def require_admin_identity(
    authorization: Annotated[str | None, Header()] = None,
) -> AdminIdentity:
    secret = os.getenv("ADMIN_SESSION_SECRET")
    if not secret or len(secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="Administrator authentication unavailable")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid administrator session")
    try:
        admin_id, _, _ = _verify_identity(authorization[7:], secret, ADMIN_ISSUER)
    except InvalidSession as exc:
        raise HTTPException(status_code=401, detail="Invalid administrator session") from exc
    return AdminIdentity(admin_id)
