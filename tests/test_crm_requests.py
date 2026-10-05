"""CRM request creation: scope, policy, idempotency, and audit contracts."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from support_system import crm_api
from support_system.admin_identity import issue_admin_session
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
from support_system.customer_identity import issue_session
from support_system.db import get_session


@pytest.fixture
def crm(monkeypatch):
    monkeypatch.setenv("CRM_ACCOUNT_READ_TOKEN", "account-secret")
    monkeypatch.setenv("CRM_BILLING_READ_TOKEN", "billing-secret")
    monkeypatch.setenv("CRM_BILLING_REQUEST_TOKEN", "request-secret")
    monkeypatch.setenv("CRM_BILLING_EXECUTE_TOKEN", "execute-secret")
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "admin-secret-that-is-at-least-32-bytes-long")
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


def admin_headers(admin_id=None) -> dict:
    token = issue_admin_session(admin_id or uuid4(), "admin-secret-that-is-at-least-32-bytes-long")
    return {"Authorization": f"Bearer {token}"}


def execute_headers(token: str = "execute-secret") -> dict:
    return {"Authorization": f"Bearer {token}"}


def execute_url(customer_id: str, action: str, request_id: str) -> str:
    return f"/internal/customers/{customer_id}/action-requests/{action}/{request_id}/execute"


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


def test_admin_refund_decision_is_scoped_audited_and_does_not_execute(crm):
    client, engine, first, _, payments, _ = crm
    created = client.post(
        f"/internal/customers/{first}/refund-requests",
        json=refund_body(payments[first]),
        headers=headers("refund-decision-1"),
    )
    request_id = created.json()["request_id"]
    url = f"/internal/action-requests/refund/{request_id}/decision"
    admin_id = uuid4()
    auth = admin_headers(admin_id)
    customer_token = issue_session(uuid4(), "admin-secret-that-is-at-least-32-bytes-long")

    assert client.post(url, json={"decision": "approve"}).status_code == 401
    assert (
        client.post(
            url, json={"decision": "approve"}, headers=headers("refund-decision-2")
        ).status_code
        == 401
    )
    assert (
        client.post(
            url,
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {customer_token}"},
        ).status_code
        == 401
    )
    assert client.post(url, json={"decision": "override"}, headers=auth).status_code == 422
    approved = client.post(url, json={"decision": "approve"}, headers=auth)
    replay = client.post(url, json={"decision": "approve"}, headers=admin_headers())
    conflict = client.post(url, json={"decision": "reject"}, headers=auth)
    assert approved.status_code == replay.status_code == 200
    assert approved.json() == replay.json()
    assert approved.json()["status"] == "approved"
    assert conflict.status_code == 409
    with Session(engine) as session:
        request = session.get(RefundRequest, request_id)
        payment = session.get(Payment, payments[first])
        audits = session.scalars(select(AuditLog).order_by(AuditLog.created_at)).all()
        assert request.status == "approved" and request.execution_reference is None
        assert (payment.refunded_cents, payment.status) == (500, "charged")
        assert len(audits) == 2
        decision = next(audit for audit in audits if audit.action == "refund.request_approved")
        assert (decision.actor_type, decision.actor_id) == ("admin", str(admin_id))
        assert decision.conversation_id == "thread-1"


def test_admin_cancellation_rejection_cannot_be_reversed(crm):
    client, engine, first, _, _, subscriptions = crm
    created = client.post(
        f"/internal/customers/{first}/cancellation-requests",
        json=cancellation_body(subscriptions[first]),
        headers=headers("cancel-decision-1"),
    )
    request_id = created.json()["request_id"]
    url = f"/internal/action-requests/cancellation/{request_id}/decision"
    auth = admin_headers()
    assert (
        client.post(url, json={"decision": "reject"}, headers=auth).json()["status"] == "rejected"
    )
    assert client.post(url, json={"decision": "reject"}, headers=auth).status_code == 200
    assert client.post(url, json={"decision": "approve"}, headers=auth).status_code == 409
    assert (
        client.post(
            f"/internal/action-requests/cancellation/{uuid4()}/decision",
            json={"decision": "approve"},
            headers=auth,
        ).status_code
        == 404
    )
    with Session(engine) as session:
        subscription = session.get(Subscription, subscriptions[first])
        assert subscription.status == "active" and subscription.cancelled_at is None
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 2
        decision = session.scalar(
            select(AuditLog).where(AuditLog.action == "cancellation.request_rejected")
        )
        assert decision.actor_type == "admin"


def test_admin_decision_fails_closed_without_secret(crm, monkeypatch):
    client, engine, _, _, _, _ = crm
    monkeypatch.delenv("ADMIN_SESSION_SECRET")
    response = client.post(
        f"/internal/action-requests/refund/{uuid4()}/decision",
        json={"decision": "approve"},
        headers=admin_headers(),
    )
    assert response.status_code == 503
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_refund_execution_requires_approval_separate_scope_and_same_conversation(crm):
    client, engine, first, second, payments, _ = crm
    created = client.post(
        f"/internal/customers/{first}/refund-requests",
        json=refund_body(payments[first]),
        headers=headers("execute-gate-1"),
    )
    request_id = created.json()["request_id"]
    url = execute_url(first, "refund", request_id)
    body = {"conversation_id": "thread-1"}
    assert client.post(url, json=body, headers=execute_headers()).status_code == 409
    for token in ("request-secret", "billing-secret"):
        assert client.post(url, json=body, headers=execute_headers(token)).status_code == 403
    assert client.post(url, json=body, headers=admin_headers()).status_code == 403
    assert client.post(url, json=body).status_code == 403
    assert (
        client.post(
            execute_url(second, "refund", request_id), json=body, headers=execute_headers()
        ).status_code
        == 404
    )
    assert (
        client.post(
            url, json={"conversation_id": "other-thread"}, headers=execute_headers()
        ).status_code
        == 404
    )
    with Session(engine) as session:
        assert session.get(Payment, payments[first]).refunded_cents == 500
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_approved_refund_executes_once_and_audits_once(crm):
    client, engine, first, _, payments, _ = crm
    created = client.post(
        f"/internal/customers/{first}/refund-requests",
        json=refund_body(payments[first]),
        headers=headers("execute-refund-1"),
    )
    request_id = created.json()["request_id"]
    decision = f"/internal/action-requests/refund/{request_id}/decision"
    assert (
        client.post(decision, json={"decision": "approve"}, headers=admin_headers()).status_code
        == 200
    )
    url = execute_url(first, "refund", request_id)
    body = {"conversation_id": "thread-1"}
    first_run = client.post(url, json=body, headers=execute_headers())
    retry = client.post(url, json=body, headers=execute_headers())
    assert first_run.status_code == retry.status_code == 200
    assert first_run.json() == retry.json()
    assert first_run.json()["status"] == "executed"
    approved_replay = client.post(decision, json={"decision": "approve"}, headers=admin_headers())
    rejected_replay = client.post(decision, json={"decision": "reject"}, headers=admin_headers())
    assert approved_replay.status_code == 200
    assert approved_replay.json()["status"] == "executed"
    assert rejected_replay.status_code == 409
    with Session(engine) as session:
        request = session.get(RefundRequest, request_id)
        payment = session.get(Payment, payments[first])
        invoice = session.get(Invoice, payment.invoice_id)
        assert request.execution_reference == f"mock-refund:{request_id}"
        assert (payment.refunded_cents, payment.status, invoice.status) == (1500, "charged", "paid")
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 3
        assert (
            session.scalar(select(AuditLog.actor_id).where(AuditLog.action == "refund.executed"))
            == "billing-executor"
        )


def test_full_refund_updates_invoice_and_payment_once(crm):
    client, engine, first, _, payments, _ = crm
    created = client.post(
        f"/internal/customers/{first}/refund-requests",
        json=refund_body(payments[first], 1500),
        headers=headers("execute-full-1"),
    )
    request_id = created.json()["request_id"]
    client.post(
        f"/internal/action-requests/refund/{request_id}/decision",
        json={"decision": "approve"},
        headers=admin_headers(),
    )
    url = execute_url(first, "refund", request_id)
    assert (
        client.post(
            url, json={"conversation_id": "thread-1"}, headers=execute_headers()
        ).status_code
        == 200
    )
    assert (
        client.post(
            url, json={"conversation_id": "thread-1"}, headers=execute_headers()
        ).status_code
        == 200
    )
    with Session(engine) as session:
        payment = session.get(Payment, payments[first])
        invoice = session.get(Invoice, payment.invoice_id)
        assert (payment.refunded_cents, payment.status, invoice.status) == (
            2000,
            "refunded",
            "refunded",
        )
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 3


def test_cancellation_execution_requires_approval_and_is_idempotent(crm):
    client, engine, first, _, _, subscriptions = crm
    created = client.post(
        f"/internal/customers/{first}/cancellation-requests",
        json=cancellation_body(subscriptions[first]),
        headers=headers("execute-cancel-1"),
    )
    request_id = created.json()["request_id"]
    url = execute_url(first, "cancellation", request_id)
    body = {"conversation_id": "thread-2"}
    assert client.post(url, json=body, headers=execute_headers()).status_code == 409
    client.post(
        f"/internal/action-requests/cancellation/{request_id}/decision",
        json={"decision": "approve"},
        headers=admin_headers(),
    )
    assert client.post(url, json=body, headers=execute_headers()).json()["status"] == "executed"
    with Session(engine) as session:
        cancelled_at = session.get(Subscription, subscriptions[first]).cancelled_at
    assert cancelled_at is not None
    assert client.post(url, json=body, headers=execute_headers()).status_code == 200
    with Session(engine) as session:
        subscription = session.get(Subscription, subscriptions[first])
        assert subscription.status == "cancelled" and subscription.cancelled_at == cancelled_at
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 3


def test_rejected_and_changed_policy_requests_cannot_execute(crm):
    client, engine, first, _, payments, subscriptions = crm
    refund = client.post(
        f"/internal/customers/{first}/refund-requests",
        json=refund_body(payments[first]),
        headers=headers("execute-rejected-1"),
    ).json()["request_id"]
    client.post(
        f"/internal/action-requests/refund/{refund}/decision",
        json={"decision": "reject"},
        headers=admin_headers(),
    )
    assert (
        client.post(
            execute_url(first, "refund", refund),
            json={"conversation_id": "thread-1"},
            headers=execute_headers(),
        ).status_code
        == 409
    )
    cancellation = client.post(
        f"/internal/customers/{first}/cancellation-requests",
        json=cancellation_body(subscriptions[first]),
        headers=headers("execute-blocked-1"),
    ).json()["request_id"]
    client.post(
        f"/internal/action-requests/cancellation/{cancellation}/decision",
        json={"decision": "approve"},
        headers=admin_headers(),
    )
    with Session(engine) as session:
        session.get(Customer, first).account_status = "suspended"
        session.commit()
    url = execute_url(first, "cancellation", cancellation)
    body = {"conversation_id": "thread-2"}
    assert client.post(url, json=body, headers=execute_headers()).status_code == 409
    assert client.post(url, json=body, headers=execute_headers()).status_code == 409
    with Session(engine) as session:
        assert session.get(CancellationRequest, cancellation).status == "approved"
        assert session.get(Subscription, subscriptions[first]).status == "active"
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "cancellation.execution_blocked")
            )
            == 1
        )


def test_refund_execution_rechecks_available_balance(crm):
    client, engine, first, _, payments, _ = crm
    request_id = client.post(
        f"/internal/customers/{first}/refund-requests",
        json=refund_body(payments[first]),
        headers=headers("execute-stale-balance-1"),
    ).json()["request_id"]
    client.post(
        f"/internal/action-requests/refund/{request_id}/decision",
        json={"decision": "approve"},
        headers=admin_headers(),
    )
    with Session(engine) as session:
        session.get(Payment, payments[first]).refunded_cents = 1500
        session.commit()
    response = client.post(
        execute_url(first, "refund", request_id),
        json={"conversation_id": "thread-1"},
        headers=execute_headers(),
    )
    assert response.status_code == 409
    with Session(engine) as session:
        assert session.get(Payment, payments[first]).refunded_cents == 1500
        assert session.get(RefundRequest, request_id).status == "approved"
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "refund.execution_blocked")
            )
            == 1
        )


def test_execution_token_configuration_fails_closed(crm, monkeypatch):
    client, _, first, _, _, _ = crm
    url = execute_url(first, "refund", str(uuid4()))
    body = {"conversation_id": "thread-1"}
    monkeypatch.delenv("CRM_BILLING_EXECUTE_TOKEN")
    assert client.post(url, json=body, headers=execute_headers()).status_code == 503
    monkeypatch.setenv("CRM_BILLING_EXECUTE_TOKEN", "request-secret")
    assert client.post(url, json=body, headers=execute_headers("request-secret")).status_code == 503
