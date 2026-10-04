"""PostgreSQL migration and seed smoke tests, enabled by RUN_POSTGRES_TESTS=1."""

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from support_system.admin_identity import issue_admin_session
from support_system.crm_api import app
from support_system.crm_models import (
    AuditLog,
    CancellationRequest,
    Customer,
    Invoice,
    Payment,
    RefundRequest,
    Subscription,
    SupportArticle,
)
from support_system.db import database_url
from support_system.seed_crm import fixture_id
from support_system.support_knowledge import answer_technical, seed_articles

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires migrated, seeded PostgreSQL"
)


def test_migrated_schema_and_repeatable_seed():
    engine = create_engine(database_url())
    try:
        assert {
            "customers",
            "subscriptions",
            "invoices",
            "payments",
            "refund_requests",
            "cancellation_requests",
            "support_tickets",
            "audit_logs",
            "support_articles",
        } <= set(inspect(engine).get_table_names())
        with Session(engine) as session:
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version"))
                == "003_support_articles"
            )
            assert session.scalar(select(func.count()).select_from(Customer)) == 37
            assert session.scalar(select(func.count()).select_from(Subscription)) == 37
            assert session.scalar(select(func.count()).select_from(Invoice)) == 37
            assert session.scalar(select(func.count()).select_from(Payment)) == 34
            assert session.scalar(select(func.count()).select_from(SupportArticle)) == 20
            assert seed_articles(session) == (0, 0)
    finally:
        engine.dispose()


def test_postgres_vector_index_and_evidence_gate():
    engine = create_engine(database_url())
    try:
        indexes = {item["name"] for item in inspect(engine).get_indexes("support_articles")}
        assert "ix_support_articles_embedding_hnsw" in indexes
        with Session(engine) as session:
            known = answer_technical(session, "Why does a saved support link show a 404 page?")
            weak = answer_technical(session, "quantum banana orbit topology")
        assert known.status == "answered"
        assert known.evidence[0].article_id == "KB-009"
        assert known.answer.endswith("[KB-009]")
        assert weak.status == "escalated" and weak.answer is None
    finally:
        engine.dispose()


def test_postgres_read_contract():
    first = fixture_id("customer", 0)
    other = fixture_id("customer", 1)
    with TestClient(app) as client:
        account = client.get(
            f"/internal/customers/{first}/account",
            headers={"Authorization": f"Bearer {os.environ['CRM_ACCOUNT_READ_TOKEN']}"},
        )
        billing = client.get(
            f"/internal/customers/{first}/billing",
            headers={"Authorization": f"Bearer {os.environ['CRM_BILLING_READ_TOKEN']}"},
        )
    assert account.status_code == billing.status_code == 200
    assert account.json()["customer_id"] == billing.json()["customer_id"] == first
    assert len(billing.json()["payments"]) == 1
    assert other not in billing.text


def test_postgres_request_idempotency_and_audit():
    customer_id = fixture_id("customer", 0)
    payment_id = fixture_id("payment", 0)
    url = f"/internal/customers/{customer_id}/refund-requests"
    headers = {
        "Authorization": f"Bearer {os.environ['CRM_BILLING_REQUEST_TOKEN']}",
        "Idempotency-Key": "integration-refund-fixture-0",
    }
    body = {
        "payment_id": payment_id,
        "amount_cents": 1000,
        "reason": "service issue",
        "conversation_id": "integration-thread-0",
    }
    with TestClient(app) as client:
        first = client.post(url, json=body, headers=headers)
        replay = client.post(url, json=body, headers=headers)
        competing = client.post(
            url, json=body, headers={**headers, "Idempotency-Key": "integration-refund-fixture-1"}
        )
    assert first.status_code in {200, 201}
    assert replay.status_code == 200 and replay.json() == first.json()
    assert competing.status_code == 409
    engine = create_engine(database_url())
    try:
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RefundRequest)
                    .where(RefundRequest.payment_id == payment_id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "refund.request_created",
                        AuditLog.customer_id == customer_id,
                    )
                )
                == 1
            )
            payment = session.get(Payment, payment_id)
            assert payment.refunded_cents == 0 and payment.status == "charged"
    finally:
        engine.dispose()


def test_postgres_admin_decision_is_persisted_once(monkeypatch):
    customer_id = fixture_id("customer", 0)
    payment_id = fixture_id("payment", 0)
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "ci-admin-session-secret-at-least-32-bytes")
    admin_id = fixture_id("admin", 0)
    auth = {
        "Authorization": f"Bearer {issue_admin_session(admin_id, 'ci-admin-session-secret-at-least-32-bytes')}"
    }
    with TestClient(app) as client:
        created = client.post(
            f"/internal/customers/{customer_id}/refund-requests",
            json={
                "payment_id": payment_id,
                "amount_cents": 1000,
                "reason": "service issue",
                "conversation_id": "integration-thread-0",
            },
            headers={
                "Authorization": f"Bearer {os.environ['CRM_BILLING_REQUEST_TOKEN']}",
                "Idempotency-Key": "integration-refund-fixture-0",
            },
        )
        assert created.status_code in {200, 201}
        url = f"/internal/action-requests/refund/{created.json()['request_id']}/decision"
        first = client.post(url, json={"decision": "approve"}, headers=auth)
        replay = client.post(url, json={"decision": "approve"}, headers=auth)
        conflict = client.post(url, json={"decision": "reject"}, headers=auth)
    assert first.status_code == replay.status_code == 200
    assert conflict.status_code == 409
    engine = create_engine(database_url())
    try:
        with Session(engine) as session:
            assert session.get(RefundRequest, created.json()["request_id"]).status == "approved"
            assert session.get(Payment, payment_id).refunded_cents == 0
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "refund.request_approved",
                        AuditLog.resource_id == created.json()["request_id"],
                    )
                )
                == 1
            )
    finally:
        engine.dispose()


def test_postgres_concurrent_refund_execution_is_single_write():
    customer_id = fixture_id("customer", 1)
    payment_id = fixture_id("payment", 1)
    with TestClient(app) as client:
        created = client.post(
            f"/internal/customers/{customer_id}/refund-requests",
            json={
                "payment_id": payment_id,
                "amount_cents": 2000,
                "reason": "service issue",
                "conversation_id": "integration-execution-thread-1",
            },
            headers={
                "Authorization": f"Bearer {os.environ['CRM_BILLING_REQUEST_TOKEN']}",
                "Idempotency-Key": "integration-execute-fixture-1",
            },
        )
        assert created.status_code == 201
        request_id = created.json()["request_id"]
        decision = client.post(
            f"/internal/action-requests/refund/{request_id}/decision",
            json={"decision": "approve"},
            headers={
                "Authorization": f"Bearer {issue_admin_session(fixture_id('admin', 1), os.environ['ADMIN_SESSION_SECRET'])}"
            },
        )
        assert decision.status_code == 200

    url = f"/internal/customers/{customer_id}/action-requests/refund/{request_id}/execute"
    headers = {"Authorization": f"Bearer {os.environ['CRM_BILLING_EXECUTE_TOKEN']}"}

    def execute():
        with TestClient(app) as client:
            return client.post(
                url, json={"conversation_id": "integration-execution-thread-1"}, headers=headers
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.json()["status"] == "executed" for response in responses)

    engine = create_engine(database_url())
    try:
        with Session(engine) as session:
            payment = session.get(Payment, payment_id)
            invoice = session.get(Invoice, payment.invoice_id)
            request = session.get(RefundRequest, request_id)
            assert (payment.refunded_cents, payment.status, invoice.status) == (
                2000,
                "refunded",
                "refunded",
            )
            assert request.status == "executed"
            assert request.execution_reference == f"mock-refund:{request_id}"
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.action == "refund.executed",
                        AuditLog.resource_id == request_id,
                    )
                )
                == 1
            )
    finally:
        engine.dispose()


def test_postgres_rejects_competing_open_actions():
    customer_id = fixture_id("customer", 2)
    payment_id = fixture_id("payment", 2)
    subscription_id = fixture_id("subscription", 2)
    engine = create_engine(database_url())
    try:
        with Session(engine) as session:
            for number in (1, 2):
                session.add(
                    RefundRequest(
                        customer_id=customer_id,
                        payment_id=payment_id,
                        amount_cents=100,
                        reason="test",
                        status="pending",
                        idempotency_key=f"index-refund-{number}",
                        conversation_id="index-check",
                    )
                )
                if number == 1:
                    session.flush()
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()
        with Session(engine) as session:
            for number in (1, 2):
                session.add(
                    CancellationRequest(
                        customer_id=customer_id,
                        subscription_id=subscription_id,
                        reason="test",
                        status="pending",
                        idempotency_key=f"index-cancel-{number}",
                        conversation_id="index-check",
                    )
                )
                if number == 1:
                    session.flush()
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RefundRequest)
                    .where(RefundRequest.idempotency_key.like("index-refund-%"))
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(CancellationRequest)
                    .where(CancellationRequest.idempotency_key.like("index-cancel-%"))
                )
                == 0
            )
    finally:
        engine.dispose()
