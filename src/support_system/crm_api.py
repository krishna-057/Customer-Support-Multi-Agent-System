"""CRM service: guarded internal reads; sensitive writes are still closed."""

import hmac
import os
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from support_system.contracts import (
    AccountRead,
    BillingRead,
    HealthResponse,
    HealthStatus,
    ServiceName,
)
from support_system.crm_models import Customer, Invoice, Payment, Subscription
from support_system.db import get_session

app = FastAPI(title="Support CRM API", version="0.1.0")


def require_scope(scope: str, authorization: str | None) -> None:
    account_token = os.getenv("CRM_ACCOUNT_READ_TOKEN")
    billing_token = os.getenv("CRM_BILLING_READ_TOKEN")
    if account_token and billing_token and hmac.compare_digest(account_token, billing_token):
        raise HTTPException(status_code=503, detail="CRM unavailable")
    names = (
        ("CRM_BILLING_READ_TOKEN",)
        if scope == "billing"
        else ("CRM_ACCOUNT_READ_TOKEN", "CRM_BILLING_READ_TOKEN")
    )
    valid_tokens = [os.getenv(name) for name in names]
    if not any(valid_tokens):
        raise HTTPException(status_code=503, detail="CRM unavailable")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if (
        not authorization
        or not authorization.startswith("Bearer ")
        or not any(hmac.compare_digest(supplied, token) for token in valid_tokens if token)
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


def account_scope(authorization: Annotated[str | None, Header()] = None) -> None:
    require_scope("account", authorization)


def billing_scope(authorization: Annotated[str | None, Header()] = None) -> None:
    require_scope("billing", authorization)


@app.exception_handler(SQLAlchemyError)
def database_error(_request, _exc: SQLAlchemyError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": "CRM unavailable"})


@app.get(
    "/internal/customers/{customer_id}/account",
    dependencies=[Depends(account_scope)],
    response_model=AccountRead,
)
def account(customer_id: UUID, session: Annotated[Session, Depends(get_session)]) -> dict:
    key = str(customer_id)
    customer = session.get(Customer, key)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    subscriptions = session.scalars(
        select(Subscription).where(Subscription.customer_id == key).order_by(Subscription.id)
    ).all()
    return {
        "customer_id": customer.id,
        "display_name": customer.display_name,
        "account_status": customer.account_status,
        "subscriptions": [
            {
                "subscription_id": item.id,
                "plan_code": item.plan_code,
                "status": item.status,
                "renews_at": item.renews_at,
                "cancelled_at": item.cancelled_at,
            }
            for item in subscriptions
        ],
    }


@app.get(
    "/internal/customers/{customer_id}/billing",
    dependencies=[Depends(billing_scope)],
    response_model=BillingRead,
)
def billing(customer_id: UUID, session: Annotated[Session, Depends(get_session)]) -> dict:
    key = str(customer_id)
    if session.get(Customer, key) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    invoices = session.scalars(
        select(Invoice).where(Invoice.customer_id == key).order_by(Invoice.issued_at.desc())
    ).all()
    payments = session.scalars(
        select(Payment).where(Payment.customer_id == key).order_by(Payment.charged_at.desc())
    ).all()
    return {
        "customer_id": key,
        "invoices": [
            {
                "invoice_id": item.id,
                "subscription_id": item.subscription_id,
                "amount_cents": item.amount_cents,
                "currency": item.currency,
                "status": item.status,
                "issued_at": item.issued_at,
                "due_at": item.due_at,
            }
            for item in invoices
        ],
        "payments": [
            {
                "payment_id": item.id,
                "invoice_id": item.invoice_id,
                "amount_cents": item.amount_cents,
                "refundable_cents": item.amount_cents - item.refunded_cents,
                "status": item.status,
                "charged_at": item.charged_at,
            }
            for item in payments
        ],
    }


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def live() -> HealthResponse:
    return HealthResponse(service=ServiceName.CRM, status=HealthStatus.OK)


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def ready(response: Response) -> HealthResponse:
    database_password = os.getenv("DB_PASSWORD")
    if not database_password:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(service=ServiceName.CRM, status=HealthStatus.UNAVAILABLE)

    try:
        with psycopg.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "support"),
            user=os.getenv("DB_USER", "support"),
            password=database_password,
            connect_timeout=2,
        ) as connection:
            connection.execute("SELECT 1")
    except psycopg.Error:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(service=ServiceName.CRM, status=HealthStatus.UNAVAILABLE)

    return HealthResponse(service=ServiceName.CRM, status=HealthStatus.OK)
