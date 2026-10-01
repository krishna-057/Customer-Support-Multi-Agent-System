"""PostgreSQL migration and seed smoke tests, enabled by RUN_POSTGRES_TESTS=1."""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from support_system.crm_api import app
from support_system.crm_models import Customer, Invoice, Payment, Subscription
from support_system.db import database_url
from support_system.seed_crm import fixture_id

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
        } <= set(inspect(engine).get_table_names())
        with Session(engine) as session:
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version")) == "001_initial_crm"
            )
            assert session.scalar(select(func.count()).select_from(Customer)) == 37
            assert session.scalar(select(func.count()).select_from(Subscription)) == 37
            assert session.scalar(select(func.count()).select_from(Invoice)) == 37
            assert session.scalar(select(func.count()).select_from(Payment)) == 34
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
