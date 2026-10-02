"""Idempotent, wholly synthetic local CRM fixtures."""

from datetime import UTC, datetime, timedelta

from faker import Faker
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from support_system.crm_models import Customer, Invoice, Payment, Subscription
from support_system.db import database_url
from support_system.fixture_ids import fixture_id

EDGE_CASES = (
    ("past_due", "active", "past_due", "unpaid"),
    ("cancelled", "cancelled", "cancelled", "paid"),
    ("partial_refund", "active", "active", "paid"),
    ("full_refund", "active", "active", "refunded"),
    ("trial", "active", "trial", "unpaid"),
    ("suspended", "suspended", "paused", "paid"),
    ("annual_plan", "active", "active", "paid"),
    ("expired", "inactive", "expired", "paid"),
    ("payment_failed", "active", "active", "unpaid"),
    ("cancel_pending", "active", "cancel_pending", "paid"),
    ("renewal_due", "active", "active", "paid"),
    ("zero_balance", "active", "active", "paid"),
)


def seed(session: Session) -> int:
    fake = Faker()
    fake.seed_instance(2042)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    created = 0
    cases = [(f"baseline_{i:02d}", "active", "active", "paid") for i in range(25)]
    cases.extend(EDGE_CASES)
    for number, (label, account_status, subscription_status, invoice_status) in enumerate(cases):
        customer_id = fixture_id("customer", number)
        if session.get(Customer, customer_id):
            continue
        created += 1
        subscription_id = fixture_id("subscription", number)
        invoice_id = fixture_id("invoice", number)
        amount = 0 if label == "zero_balance" else (12000 if label == "annual_plan" else 2000)
        session.add(
            Customer(
                id=customer_id,
                email=f"fixture-{number:02d}@example.test",
                display_name=fake.name(),
                account_status=account_status,
                created_at=now,
            )
        )
        session.flush()
        session.add(
            Subscription(
                id=subscription_id,
                customer_id=customer_id,
                plan_code="annual" if label == "annual_plan" else "monthly",
                status=subscription_status,
                renews_at=now + timedelta(days=365 if label == "annual_plan" else 30),
                cancelled_at=now if label == "cancelled" else None,
            )
        )
        session.flush()
        session.add(
            Invoice(
                id=invoice_id,
                customer_id=customer_id,
                subscription_id=subscription_id,
                amount_cents=amount,
                currency="USD",
                status=invoice_status,
                issued_at=now,
                due_at=now + timedelta(days=14),
            )
        )
        session.flush()
        if invoice_status in {"paid", "refunded"}:
            refunded = (
                amount if label == "full_refund" else (500 if label == "partial_refund" else 0)
            )
            session.add(
                Payment(
                    id=fixture_id("payment", number),
                    customer_id=customer_id,
                    invoice_id=invoice_id,
                    amount_cents=amount,
                    refunded_cents=refunded,
                    provider_reference=f"fixture-payment-{number:02d}",
                    status="refunded" if label == "full_refund" else "charged",
                    charged_at=now + timedelta(days=1),
                )
            )
    session.commit()
    return created


def main() -> None:
    engine = create_engine(database_url())
    try:
        with Session(engine) as session:
            count = seed(session)
        print(f"Seeded {count} synthetic customers")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
