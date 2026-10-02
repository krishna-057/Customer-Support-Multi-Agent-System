"""Deterministic synthetic fulfillment data and upstream-failure cases."""

from datetime import UTC, datetime

from support_system.fixture_ids import fixture_id

FIRST_CUSTOMER = fixture_id("customer", 0)
SECOND_CUSTOMER = fixture_id("customer", 1)


def timestamp(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=UTC)


ORDERS = {
    fixture_id("order", 0): {
        "order_id": fixture_id("order", 0),
        "customer_id": FIRST_CUSTOMER,
        "status": "in_transit",
        "carrier": "Fixture Parcel",
        "eta_at": timestamp(7),
        "last_updated_at": timestamp(4),
    },
    fixture_id("order", 1): {
        "order_id": fixture_id("order", 1),
        "customer_id": SECOND_CUSTOMER,
        "status": "delivered",
        "carrier": "Fixture Parcel",
        "eta_at": None,
        "last_updated_at": timestamp(3),
    },
    fixture_id("order", 2): {
        "order_id": fixture_id("order", 2),
        "customer_id": FIRST_CUSTOMER,
        "status": "exception",
        "carrier": "Fixture Parcel",
        "eta_at": None,
        "last_updated_at": timestamp(5),
    },
}

EVENTS = {
    fixture_id("order", 0): [
        {"code": "created", "description": "Order created", "occurred_at": timestamp(1)},
        {"code": "picked_up", "description": "Parcel collected", "occurred_at": timestamp(2)},
        {"code": "in_transit", "description": "Parcel in transit", "occurred_at": timestamp(4)},
    ],
    fixture_id("order", 1): [
        {"code": "created", "description": "Order created", "occurred_at": timestamp(1)},
        {"code": "delivered", "description": "Parcel delivered", "occurred_at": timestamp(3)},
    ],
    fixture_id("order", 2): [
        {"code": "created", "description": "Order created", "occurred_at": timestamp(1)},
        {
            "code": "delivery_exception",
            "description": "Delivery needs carrier review",
            "occurred_at": timestamp(5),
        },
    ],
}

# Reserved IDs emulate upstream failures; they do not appear in order lists.
FAILURES = {
    fixture_id("order", 90): (429, "Rate limited", {"Retry-After": "2"}),
    fixture_id("order", 91): (503, "Logistics unavailable", {}),
    fixture_id("order", 92): (504, "Tracking provider timed out", {}),
}
