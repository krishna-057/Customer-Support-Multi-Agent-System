"""Service-token and customer-scope contracts for internal CRM reads."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from support_system import crm_api
from support_system.crm_models import Base, Customer, Invoice, Payment, Subscription
from support_system.db import get_session


@pytest.fixture
def crm_client(monkeypatch):
    monkeypatch.setenv("CRM_ACCOUNT_READ_TOKEN", "account-secret")
    monkeypatch.setenv("CRM_BILLING_READ_TOKEN", "billing-secret")
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    first, second = str(uuid4()), str(uuid4())
    now = datetime(2026, 9, 1, tzinfo=UTC)
    with Session(engine) as session:
        for customer_id, suffix in ((first, "one"), (second, "two")):
            subscription_id, invoice_id = str(uuid4()), str(uuid4())
            session.add(
                Customer(
                    id=customer_id,
                    email=f"{suffix}@example.test",
                    display_name=suffix,
                    account_status="active",
                    created_at=now,
                )
            )
            session.add(
                Subscription(
                    id=subscription_id,
                    customer_id=customer_id,
                    plan_code="monthly",
                    status="active",
                    renews_at=now,
                )
            )
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
            session.add(
                Payment(
                    id=str(uuid4()),
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
            yield client, first, second
    finally:
        crm_api.app.dependency_overrides.clear()
        engine.dispose()


def test_reads_fail_closed_without_service_token(crm_client):
    client, first, _ = crm_client
    for route in ("account", "billing"):
        url = f"/internal/customers/{first}/{route}"
        assert client.get(url).status_code == 403
        assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 403


def test_account_scope_cannot_read_billing(crm_client):
    client, first, _ = crm_client
    headers = {"Authorization": "Bearer account-secret"}
    assert client.get(f"/internal/customers/{first}/account", headers=headers).status_code == 200
    assert client.get(f"/internal/customers/{first}/billing", headers=headers).status_code == 403


def test_reads_are_customer_scoped_and_hide_payment_reference(crm_client):
    client, first, second = crm_client
    headers = {"Authorization": "Bearer billing-secret"}
    account = client.get(f"/internal/customers/{first}/account", headers=headers)
    billing = client.get(f"/internal/customers/{first}/billing", headers=headers)
    other = client.get(f"/internal/customers/{second}/billing", headers=headers)
    assert account.status_code == billing.status_code == other.status_code == 200
    assert account.json()["customer_id"] == first
    assert len(account.json()["subscriptions"]) == 1
    assert billing.json()["customer_id"] == first
    assert len(billing.json()["invoices"]) == len(billing.json()["payments"]) == 1
    assert billing.json()["payments"][0]["refundable_cents"] == 1500
    assert "provider_reference" not in billing.text
    assert first not in other.text


def test_unknown_customer_and_invalid_id(crm_client):
    client, _, _ = crm_client
    headers = {"Authorization": "Bearer billing-secret"}
    assert client.get(f"/internal/customers/{uuid4()}/account", headers=headers).status_code == 404
    assert client.get("/internal/customers/not-a-uuid/account", headers=headers).status_code == 422


def test_missing_token_configuration_fails_closed(crm_client, monkeypatch):
    client, first, _ = crm_client
    monkeypatch.delenv("CRM_ACCOUNT_READ_TOKEN")
    monkeypatch.delenv("CRM_BILLING_READ_TOKEN")
    response = client.get(f"/internal/customers/{first}/account")
    assert response.status_code == 503
    assert "secret" not in response.text


def test_shared_scope_token_configuration_fails_closed(crm_client, monkeypatch):
    client, first, _ = crm_client
    monkeypatch.setenv("CRM_BILLING_READ_TOKEN", "account-secret")
    response = client.get(
        f"/internal/customers/{first}/billing",
        headers={"Authorization": "Bearer account-secret"},
    )
    assert response.status_code == 503


def test_database_configuration_failure_is_generic(monkeypatch):
    monkeypatch.setenv("CRM_ACCOUNT_READ_TOKEN", "account-secret")
    monkeypatch.setenv("CRM_BILLING_READ_TOKEN", "billing-secret")
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    with TestClient(crm_api.app) as client:
        response = client.get(
            f"/internal/customers/{uuid4()}/account",
            headers={"Authorization": "Bearer account-secret"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "CRM unavailable"}
