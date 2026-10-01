"""CRM request creation: scope, policy, idempotency, and audit contracts."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from support_system import crm_api
from support_system.crm_models import (
    AuditLog,
    Base,
    CancellationRequest,
    Customer,
    Invoice,
    Payment,
    RefundRequest,
    Subscription,
)
from support_system.db import get_session


@pytest.fixture
def crm(monkeypatch):
    monkeypatch.setenv("CRM_ACCOUNT_READ_TOKEN", "account-secret")
    monkeypatch.setenv("CRM_BILLING_READ_TOKEN", "billing-secret")
    monkeypatch.setenv("CRM_BILLING_REQUEST_TOKEN", "request-secret")
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    first, second = str(uuid4()), str(uuid4())
    payments, subscriptions = {}, {}
    now = datetime(2026, 9, 1, tzinfo=UTC)
    with Session(engine) as session:
        for customer_id, suffix in ((first, "one"), (second, "two")):
            subscription_id, invoice_id, payment_id = str(uuid4()), str(uuid4()), str(uuid4())
            payments[customer_id], subscriptions[customer_id] = payment_id, subscription_id
            session.add(
                Customer(
                    id=customer_id,
                    email=f"{suffix}@example.test",
                    display_name=suffix,
                    account_status="active",
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                Subscription(
                    id=subscription_id,
                    customer_id=customer_id,
                    plan_code="monthly",
                    status="active",
                    renews_at=now,
                )
            )
            session.flush()
            session.add(
                Invoice(
                    id=invoice_id,
                    customer_id=customer_id,
                    subscription_id=subscription_id,
                    amount_cents=2000,
                    currency="USD",
                    status="paid",
                    issued_at=now,
                    due_at=now,
                )
            )
            session.flush()
            session.add(
                Payment(
                    id=payment_id,
                    customer_id=customer_id,
                    invoice_id=invoice_id,
                    amount_cents=2000,
                    refunded_cents=500,
                    provider_reference=f"fixture-{suffix}",
                    status="charged",
                    charged_at=now,
                )
            )
        session.commit()

    def session_override():
        with Session(engine) as session:
            yield session

    crm_api.app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(crm_api.app) as client:
            yield client, engine, first, second, payments, subscriptions
    finally:
        crm_api.app.dependency_overrides.clear()
        engine.dispose()


def headers(key: str, token: str = "request-secret") -> dict:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


def refund_body(payment_id: str, amount: int = 1000) -> dict:
    return {
        "payment_id": payment_id,
        "amount_cents": amount,
        "reason": "service issue",
        "conversation_id": "thread-1",
    }


def cancellation_body(subscription_id: str) -> dict:
    return {
        "subscription_id": subscription_id,
        "reason": "no longer needed",
        "conversation_id": "thread-2",
    }


def test_request_scope_and_validation_fail_closed(crm):
    client, engine, first, _, payments, _ = crm
    url = f"/internal/customers/{first}/refund-requests"
    body = refund_body(payments[first])
    assert (
        client.post(url, json=body, headers=headers("key-0001", "billing-secret")).status_code
        == 403
    )
    assert client.post(url, json=body, headers={"Idempotency-Key": "key-0001"}).status_code == 403
    assert (
        client.post(url, json=body, headers={"Authorization": "Bearer request-secret"}).status_code
        == 422
    )
    assert client.post(url, json=body, headers=headers("bad key 1")).status_code == 422
    assert (
        client.post(
            url, json=refund_body(payments[first], -1), headers=headers("key-0001")
        ).status_code
        == 422
    )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RefundRequest)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_refund_request_replay_conflict_and_no_execution(crm):
    client, engine, first, _, payments, _ = crm
    url = f"/internal/customers/{first}/refund-requests"
    body = refund_body(payments[first])
    created = client.post(url, json=body, headers=headers("refund-key-1"))
    replay = client.post(url, json=body, headers=headers("refund-key-1"))
    changed = client.post(
        url, json=refund_body(payments[first], 800), headers=headers("refund-key-1")
    )
    competing = client.post(url, json=body, headers=headers("refund-key-2"))
    assert created.status_code == 201
    assert replay.status_code == 200 and replay.json() == created.json()
    assert created.json()["status"] == "pending"
    assert changed.status_code == competing.status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RefundRequest)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1
        payment = session.get(Payment, payments[first])
        assert (payment.refunded_cents, payment.status) == (500, "charged")


def test_refund_policy_denial_is_audited(crm):
    client, engine, first, second, payments, _ = crm
    url = f"/internal/customers/{first}/refund-requests"
    assert (
        client.post(
            url, json=refund_body(payments[second]), headers=headers("cross-customer-1")
        ).status_code
        == 404
    )
    denied = client.post(
        url, json=refund_body(payments[first], 1501), headers=headers("refund-denied-1")
    )
    assert denied.status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RefundRequest)) == 0
        logs = session.scalars(select(AuditLog)).all()
        assert len(logs) == 1
        assert logs[0].action == "refund.request_denied"
        assert logs[0].result == {
            "request": {"payment_id": payments[first], "amount_cents": 1501},
            "outcome": {"code": "policy_ineligible"},
        }


def test_cancellation_request_replay_and_policy(crm):
    client, engine, first, second, _, subscriptions = crm
    url = f"/internal/customers/{first}/cancellation-requests"
    body = cancellation_body(subscriptions[first])
    assert (
        client.post(
            url, json=cancellation_body(subscriptions[second]), headers=headers("cross-customer-2")
        ).status_code
        == 404
    )
    created = client.post(url, json=body, headers=headers("cancel-key-1"))
    replay = client.post(url, json=body, headers=headers("cancel-key-1"))
    competing = client.post(url, json=body, headers=headers("cancel-key-2"))
    assert created.status_code == 201
    assert replay.status_code == 200 and replay.json() == created.json()
    assert competing.status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(CancellationRequest)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1
        subscription = session.get(Subscription, subscriptions[first])
        assert subscription.status == "active" and subscription.cancelled_at is None


def test_inactive_customer_cannot_request_cancellation(crm):
    client, engine, first, _, _, subscriptions = crm
    with Session(engine) as session:
        session.get(Customer, first).account_status = "suspended"
        session.commit()
    response = client.post(
        f"/internal/customers/{first}/cancellation-requests",
        json=cancellation_body(subscriptions[first]),
        headers=headers("cancel-denied-1"),
    )
    assert response.status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(CancellationRequest)) == 0
        assert session.scalar(select(AuditLog.action)) == "cancellation.request_denied"
