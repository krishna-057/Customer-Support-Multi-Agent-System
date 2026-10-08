"""CRM service: guarded internal reads and approval-bound action requests."""

import hmac
import os
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from support_system.admin_identity import AdminIdentity, require_admin_identity
from support_system.contracts import (
    AccountRead,
    ActionDecisionInput,
    ActionExecutionInput,
    ActionRequestRead,
    BillingRead,
    CancellationRequestInput,
    HealthResponse,
    HealthStatus,
    RefundRequestInput,
    ReviewQueueRead,
    ServiceName,
    TicketCreateInput,
    TicketQueueRead,
    TicketRead,
)
from support_system.crm_actions import (
    create_cancellation_request,
    create_refund_request,
    decide_action_request,
    execute_action_request,
)
from support_system.crm_models import (
    CancellationRequest,
    Customer,
    Invoice,
    Payment,
    RefundRequest,
    Subscription,
    SupportTicket,
)
from support_system.db import get_session

app = FastAPI(title="Support CRM API", version="0.1.0")


def escalation_scope(authorization: Annotated[str | None, Header()] = None) -> None:
    require_scope("escalation", authorization)


def _ticket_read(item: SupportTicket) -> TicketRead:
    return TicketRead(
        ticket_id=item.id,
        customer_id=item.customer_id,
        conversation_id=item.conversation_id,
        summary=item.summary,
        priority=item.priority,
        status=item.status,
        created_at=item.created_at,
    )


@app.post("/internal/customers/{customer_id}/tickets", response_model=TicketRead, status_code=201)
def create_ticket(
    customer_id: UUID,
    body: TicketCreateInput,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    _scope: Annotated[None, Depends(escalation_scope)],
) -> TicketRead:
    response.headers["Cache-Control"] = "no-store"
    key = str(customer_id)
    if session.get(Customer, key) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    existing = session.scalar(
        select(SupportTicket).where(
            SupportTicket.customer_id == key,
            SupportTicket.conversation_id == str(body.conversation_id),
        )
    )
    if existing:
        if existing.summary != body.reason:
            raise HTTPException(status_code=409, detail="Conversation already escalated")
        response.status_code = 200
        return _ticket_read(existing)
    item = SupportTicket(
        customer_id=key,
        conversation_id=str(body.conversation_id),
        summary=body.reason,
        priority="high" if body.reason.endswith(("unavailable", "timeout")) else "normal",
        status="open",
    )
    session.add(item)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(SupportTicket).where(
                SupportTicket.customer_id == key,
                SupportTicket.conversation_id == str(body.conversation_id),
            )
        )
        if existing and existing.summary == body.reason:
            response.status_code = 200
            return _ticket_read(existing)
        raise HTTPException(status_code=409, detail="Conversation already escalated") from None
    return _ticket_read(item)


@app.get("/internal/tickets", response_model=TicketQueueRead)
def review_tickets(
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    _admin: Annotated[AdminIdentity, Depends(require_admin_identity)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> TicketQueueRead:
    rows = session.scalars(
        select(SupportTicket)
        .where(SupportTicket.status == "open")
        .order_by(SupportTicket.created_at, SupportTicket.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return TicketQueueRead(
        items=[_ticket_read(item) for item in rows[:limit]],
        offset=offset,
        has_more=len(rows) > limit,
    )


@app.get("/internal/action-requests/review", response_model=ReviewQueueRead)
def review_action_requests(
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    _admin: Annotated[AdminIdentity, Depends(require_admin_identity)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    rows = []
    for action, model in (("refund", RefundRequest), ("cancellation", CancellationRequest)):
        candidates = session.scalars(
            select(model)
            .where(model.status.in_(("pending", "approved")))
            .order_by(model.created_at, model.id)
            .limit(offset + limit + 1)
        ).all()
        for item in candidates:
            rows.append(
                {
                    "request_id": item.id,
                    "customer_id": item.customer_id,
                    "conversation_id": item.conversation_id,
                    "action": action,
                    "target_id": item.payment_id if action == "refund" else item.subscription_id,
                    "amount_cents": item.amount_cents if action == "refund" else None,
                    "reason": item.reason,
                    "status": item.status,
                    "created_at": item.created_at,
                }
            )
    rows.sort(key=lambda item: (item["created_at"], item["action"], item["request_id"]))
    page = rows[offset : offset + limit]
    return {"items": page, "offset": offset, "has_more": len(rows) > offset + limit}


def require_scope(scope: str, authorization: str | None) -> None:
    account_token = os.getenv("CRM_ACCOUNT_READ_TOKEN")
    billing_token = os.getenv("CRM_BILLING_READ_TOKEN")
    request_token = os.getenv("CRM_BILLING_REQUEST_TOKEN")
    execute_token = os.getenv("CRM_BILLING_EXECUTE_TOKEN")
    escalation_token = os.getenv("CRM_ESCALATION_WRITE_TOKEN")
    configured = [
        token
        for token in (account_token, billing_token, request_token, execute_token, escalation_token)
        if token
    ]
    if len(configured) != len(set(configured)):
        raise HTTPException(status_code=503, detail="CRM unavailable")
    names = {
        "account": ("CRM_ACCOUNT_READ_TOKEN", "CRM_BILLING_READ_TOKEN"),
        "billing": ("CRM_BILLING_READ_TOKEN",),
        "request": ("CRM_BILLING_REQUEST_TOKEN",),
        "execute": ("CRM_BILLING_EXECUTE_TOKEN",),
        "escalation": ("CRM_ESCALATION_WRITE_TOKEN",),
    }[scope]
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


def request_scope(authorization: Annotated[str | None, Header()] = None) -> None:
    require_scope("request", authorization)


def execute_scope(authorization: Annotated[str | None, Header()] = None) -> None:
    require_scope("execute", authorization)


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


@app.post(
    "/internal/customers/{customer_id}/refund-requests",
    dependencies=[Depends(request_scope)],
    response_model=ActionRequestRead,
    status_code=201,
)
def request_refund(
    customer_id: UUID,
    body: RefundRequestInput,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[
        str, Header(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    ],
) -> dict:
    item, created = create_refund_request(session, str(customer_id), body, idempotency_key)
    if not created:
        response.status_code = 200
    return {
        "request_id": item.id,
        "customer_id": item.customer_id,
        "target_id": item.payment_id,
        "action": "refund",
        "status": item.status,
    }


@app.post(
    "/internal/customers/{customer_id}/cancellation-requests",
    dependencies=[Depends(request_scope)],
    response_model=ActionRequestRead,
    status_code=201,
)
def request_cancellation(
    customer_id: UUID,
    body: CancellationRequestInput,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[
        str, Header(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    ],
) -> dict:
    item, created = create_cancellation_request(session, str(customer_id), body, idempotency_key)
    if not created:
        response.status_code = 200
    return {
        "request_id": item.id,
        "customer_id": item.customer_id,
        "target_id": item.subscription_id,
        "action": "cancellation",
        "status": item.status,
    }


@app.post(
    "/internal/action-requests/{action}/{request_id}/decision",
    response_model=ActionRequestRead,
)
def decide_request(
    action: Literal["refund", "cancellation"],
    request_id: UUID,
    body: ActionDecisionInput,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    admin: Annotated[AdminIdentity, Depends(require_admin_identity)],
) -> dict:
    item, changed = decide_action_request(
        session, action, str(request_id), body.decision, str(admin.admin_id)
    )
    if not changed:
        response.status_code = 200
    return {
        "request_id": item.id,
        "customer_id": item.customer_id,
        "target_id": item.payment_id if action == "refund" else item.subscription_id,
        "action": action,
        "status": item.status,
    }


@app.post(
    "/internal/customers/{customer_id}/action-requests/{action}/{request_id}/execute",
    dependencies=[Depends(execute_scope)],
    response_model=ActionRequestRead,
)
def execute_request(
    customer_id: UUID,
    action: Literal["refund", "cancellation"],
    request_id: UUID,
    body: ActionExecutionInput,
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    item = execute_action_request(
        session, str(customer_id), action, str(request_id), body.conversation_id
    )
    return {
        "request_id": item.id,
        "customer_id": item.customer_id,
        "target_id": item.payment_id if action == "refund" else item.subscription_id,
        "action": action,
        "status": item.status,
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
