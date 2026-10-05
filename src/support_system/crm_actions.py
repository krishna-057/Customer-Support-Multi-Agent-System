"""Deterministic CRM request policy, idempotency, and audit persistence."""

from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from support_system.contracts import CancellationRequestInput, RefundRequestInput
from support_system.crm_models import (
    AuditLog,
    CancellationRequest,
    Customer,
    Invoice,
    Payment,
    RefundRequest,
    Subscription,
    utc_now,
)


def _audit(
    session: Session,
    *,
    customer_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    event_key: str,
    conversation_id: str,
    request_payload: dict,
    outcome: dict,
    actor_type: str = "service",
    actor_id: str = "billing-agent",
) -> None:
    session.add(
        AuditLog(
            customer_id=customer_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            event_key=event_key,
            conversation_id=conversation_id,
            result={"request": request_payload, "outcome": outcome},
        )
    )


def _deny(
    session: Session,
    *,
    customer_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    conversation_id: str,
    code: str,
    request_payload: dict,
) -> None:
    _audit(
        session,
        customer_id=customer_id,
        action=f"{action}.request_denied",
        resource_type=resource_type,
        resource_id=resource_id,
        event_key=str(uuid4()),
        conversation_id=conversation_id,
        request_payload=request_payload,
        outcome={"code": code},
    )
    session.commit()
    raise HTTPException(status_code=409, detail="Action not eligible")


def _same_refund(item: RefundRequest, customer_id: str, body: RefundRequestInput) -> bool:
    return (
        item.customer_id == customer_id
        and item.payment_id == str(body.payment_id)
        and item.amount_cents == body.amount_cents
        and item.reason == body.reason
        and item.conversation_id == body.conversation_id
    )


def _same_cancellation(
    item: CancellationRequest, customer_id: str, body: CancellationRequestInput
) -> bool:
    return (
        item.customer_id == customer_id
        and item.subscription_id == str(body.subscription_id)
        and item.reason == body.reason
        and item.conversation_id == body.conversation_id
    )


def create_refund_request(
    session: Session, customer_id: str, body: RefundRequestInput, key: str
) -> tuple[RefundRequest, bool]:
    existing = session.scalar(select(RefundRequest).where(RefundRequest.idempotency_key == key))
    if existing is not None:
        if _same_refund(existing, customer_id, body):
            return existing, False
        raise HTTPException(status_code=409, detail="Idempotency key conflict")

    customer = session.get(Customer, customer_id)
    payment = session.scalar(
        select(Payment).where(
            Payment.id == str(body.payment_id), Payment.customer_id == customer_id
        )
    )
    if customer is None or payment is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    invoice = session.scalar(
        select(Invoice).where(Invoice.id == payment.invoice_id, Invoice.customer_id == customer_id)
    )
    if (
        customer.account_status != "active"
        or invoice is None
        or invoice.status != "paid"
        or payment.status != "charged"
        or body.amount_cents > payment.amount_cents - payment.refunded_cents
    ):
        _deny(
            session,
            customer_id=customer_id,
            action="refund",
            resource_type="payment",
            resource_id=payment.id,
            conversation_id=body.conversation_id,
            code="policy_ineligible",
            request_payload={"payment_id": payment.id, "amount_cents": body.amount_cents},
        )

    request = RefundRequest(
        customer_id=customer_id,
        payment_id=payment.id,
        amount_cents=body.amount_cents,
        reason=body.reason,
        status="pending",
        idempotency_key=key,
        conversation_id=body.conversation_id,
    )
    try:
        session.add(request)
        session.flush()
        _audit(
            session,
            customer_id=customer_id,
            action="refund.request_created",
            resource_type="refund_request",
            resource_id=request.id,
            event_key=key,
            conversation_id=body.conversation_id,
            request_payload={"payment_id": payment.id, "amount_cents": body.amount_cents},
            outcome={"status": "pending"},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(RefundRequest).where(RefundRequest.idempotency_key == key))
        if existing is not None and _same_refund(existing, customer_id, body):
            return existing, False
        raise HTTPException(status_code=409, detail="Action already requested") from None
    return request, True


def create_cancellation_request(
    session: Session, customer_id: str, body: CancellationRequestInput, key: str
) -> tuple[CancellationRequest, bool]:
    existing = session.scalar(
        select(CancellationRequest).where(CancellationRequest.idempotency_key == key)
    )
    if existing is not None:
        if _same_cancellation(existing, customer_id, body):
            return existing, False
        raise HTTPException(status_code=409, detail="Idempotency key conflict")

    customer = session.get(Customer, customer_id)
    subscription = session.scalar(
        select(Subscription).where(
            Subscription.id == str(body.subscription_id), Subscription.customer_id == customer_id
        )
    )
    if customer is None or subscription is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    if (
        customer.account_status != "active"
        or subscription.status != "active"
        or subscription.cancelled_at is not None
    ):
        _deny(
            session,
            customer_id=customer_id,
            action="cancellation",
            resource_type="subscription",
            resource_id=subscription.id,
            conversation_id=body.conversation_id,
            code="policy_ineligible",
            request_payload={"subscription_id": subscription.id},
        )

    request = CancellationRequest(
        customer_id=customer_id,
        subscription_id=subscription.id,
        reason=body.reason,
        status="pending",
        idempotency_key=key,
        conversation_id=body.conversation_id,
    )
    try:
        session.add(request)
        session.flush()
        _audit(
            session,
            customer_id=customer_id,
            action="cancellation.request_created",
            resource_type="cancellation_request",
            resource_id=request.id,
            event_key=key,
            conversation_id=body.conversation_id,
            request_payload={"subscription_id": subscription.id},
            outcome={"status": "pending"},
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(CancellationRequest).where(CancellationRequest.idempotency_key == key)
        )
        if existing is not None and _same_cancellation(existing, customer_id, body):
            return existing, False
        raise HTTPException(status_code=409, detail="Action already requested") from None
    return request, True


def decide_action_request(
    session: Session, action: str, request_id: str, decision: str, admin_id: str
) -> tuple[RefundRequest | CancellationRequest, bool]:
    model = RefundRequest if action == "refund" else CancellationRequest
    item = session.scalar(select(model).where(model.id == request_id).with_for_update())
    if item is None:
        raise HTTPException(status_code=404, detail="Request not found")

    status = "approved" if decision == "approve" else "rejected"
    if item.status == status or (item.status == "executed" and decision == "approve"):
        return item, False
    if item.status != "pending":
        raise HTTPException(status_code=409, detail="Request already decided")

    item.status = status
    _audit(
        session,
        customer_id=item.customer_id,
        action=f"{action}.request_{status}",
        resource_type=f"{action}_request",
        resource_id=item.id,
        event_key=item.id,
        conversation_id=item.conversation_id,
        request_payload={},
        outcome={"status": status},
        actor_type="admin",
        actor_id=admin_id,
    )
    session.commit()
    return item, True


def _execution_blocked(
    session: Session,
    item: RefundRequest | CancellationRequest,
    action: str,
    code: str,
) -> None:
    existing = session.scalar(
        select(AuditLog).where(
            AuditLog.action == f"{action}.execution_blocked",
            AuditLog.resource_type == f"{action}_request",
            AuditLog.resource_id == item.id,
            AuditLog.event_key == item.id,
        )
    )
    if existing is None:
        _audit(
            session,
            customer_id=item.customer_id,
            action=f"{action}.execution_blocked",
            resource_type=f"{action}_request",
            resource_id=item.id,
            event_key=item.id,
            conversation_id=item.conversation_id,
            request_payload={},
            outcome={"code": code},
            actor_id="billing-executor",
        )
        session.commit()
    raise HTTPException(status_code=409, detail="Action no longer eligible")


def execute_action_request(
    session: Session,
    customer_id: str,
    action: Literal["refund", "cancellation"],
    request_id: str,
    conversation_id: str,
) -> RefundRequest | CancellationRequest:
    model = RefundRequest if action == "refund" else CancellationRequest
    item = session.scalar(
        select(model)
        .where(model.id == request_id, model.customer_id == customer_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if item.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Request not found")
    if item.status == "executed":
        return item
    if item.status != "approved":
        raise HTTPException(status_code=409, detail="Request not approved")

    customer = session.scalar(select(Customer).where(Customer.id == customer_id).with_for_update())
    if customer is None or customer.account_status != "active":
        _execution_blocked(session, item, action, "account_inactive")

    if action == "refund":
        payment = session.scalar(
            select(Payment)
            .where(Payment.id == item.payment_id, Payment.customer_id == customer_id)
            .with_for_update()
        )
        if payment is None:
            _execution_blocked(session, item, action, "payment_unavailable")
        invoice = session.scalar(
            select(Invoice)
            .where(Invoice.id == payment.invoice_id, Invoice.customer_id == customer_id)
            .with_for_update()
        )
        if (
            invoice is None
            or invoice.status != "paid"
            or payment.status != "charged"
            or item.amount_cents > payment.amount_cents - payment.refunded_cents
        ):
            _execution_blocked(session, item, action, "refund_ineligible")
        payment.refunded_cents += item.amount_cents
        if payment.refunded_cents == payment.amount_cents:
            payment.status = "refunded"
            invoice.status = "refunded"
        item.execution_reference = f"mock-refund:{item.id}"
        outcome = {"status": "executed", "amount_cents": item.amount_cents}
    else:
        subscription = session.scalar(
            select(Subscription)
            .where(Subscription.id == item.subscription_id, Subscription.customer_id == customer_id)
            .with_for_update()
        )
        if (
            subscription is None
            or subscription.status != "active"
            or subscription.cancelled_at is not None
        ):
            _execution_blocked(session, item, action, "subscription_ineligible")
        subscription.status = "cancelled"
        subscription.cancelled_at = utc_now()
        outcome = {"status": "executed"}

    item.status = "executed"
    _audit(
        session,
        customer_id=customer_id,
        action=f"{action}.executed",
        resource_type=f"{action}_request",
        resource_id=item.id,
        event_key=item.id,
        conversation_id=item.conversation_id,
        request_payload={},
        outcome=outcome,
        actor_id="billing-executor",
    )
    session.commit()
    return item
