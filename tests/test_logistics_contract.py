"""Stable customer-scoped logistics tool responses and failure cases."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from support_system.fixture_ids import fixture_id
from support_system.logistics_api import app

FIRST = fixture_id("customer", 0)
SECOND = fixture_id("customer", 1)
HEADERS = {"Authorization": "Bearer logistics-test-secret"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LOGISTICS_ORDER_READ_TOKEN", "logistics-test-secret")
    with TestClient(app) as test_client:
        yield test_client


def order_url(customer: str, order_number: int) -> str:
    return f"/internal/customers/{customer}/orders/{fixture_id('order', order_number)}/tracking"


def test_order_list_and_tracking_contract(client):
    first = client.get(f"/internal/customers/{FIRST}/orders", headers=HEADERS)
    second = client.get(f"/internal/customers/{SECOND}/orders", headers=HEADERS)
    assert first.status_code == second.status_code == 200
    assert {row["order_id"] for row in first.json()["orders"]} == {
        fixture_id("order", 0),
        fixture_id("order", 2),
    }
    assert [row["order_id"] for row in second.json()["orders"]] == [fixture_id("order", 1)]
    tracking = client.get(order_url(FIRST, 0), headers=HEADERS)
    assert tracking.status_code == 200
    assert tracking.json()["order"]["status"] == "in_transit"
    assert tracking.json()["order"]["eta_at"] is not None
    assert [event["code"] for event in tracking.json()["events"]] == [
        "created",
        "picked_up",
        "in_transit",
    ]
    assert all(row["customer_id"] == FIRST for row in first.json()["orders"])


def test_read_scope_fails_closed(client, monkeypatch):
    url = order_url(FIRST, 0)
    assert client.get(url).status_code == 403
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 403
    monkeypatch.delenv("LOGISTICS_ORDER_READ_TOKEN")
    assert client.get(url, headers=HEADERS).status_code == 503


def test_unknown_and_cross_customer_orders_have_same_404(client):
    unknown = client.get(order_url(FIRST, 3), headers=HEADERS)
    other_customer = client.get(order_url(FIRST, 1), headers=HEADERS)
    assert unknown.status_code == other_customer.status_code == 404
    assert unknown.json() == other_customer.json()
    assert client.get(f"/internal/customers/{FIRST}/orders/not-a-uuid/tracking", headers=HEADERS).status_code == 422
    assert client.get(f"/internal/customers/{uuid4()}/orders", headers=HEADERS).json()["orders"] == []


@pytest.mark.parametrize(
    ("number", "status", "message"),
    [
        (90, 429, "Rate limited"),
        (91, 503, "Logistics unavailable"),
        (92, 504, "Tracking provider timed out"),
    ],
)
def test_repeatable_provider_failures(client, number, status, message):
    response = client.get(order_url(FIRST, number), headers=HEADERS)
    assert response.status_code == status
    assert response.json() == {"detail": message}
    if status == 429:
        assert response.headers["Retry-After"] == "2"
    assert client.get(order_url(SECOND, number), headers=HEADERS).status_code == 404
