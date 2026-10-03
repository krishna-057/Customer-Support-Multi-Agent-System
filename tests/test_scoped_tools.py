"""Scoped adapters use only their service credentials and validate ownership."""

import json
from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest

from support_system import scoped_tools
from support_system.scoped_tools import LogisticsTool, TechnicalTool, ToolFailure


class FakeResponse:
    def __init__(self, value):
        self.value = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.value


def test_technical_adapter_uses_only_retrieval_credential(monkeypatch):
    monkeypatch.setenv("TECHNICAL_RETRIEVAL_TOKEN", "technical-secret")
    monkeypatch.setenv("TECHNICAL_API_URL", "http://technical.test:8000")
    calls = []

    def fake_open(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(
            {
                "status": "answered",
                "answer": "Check your settings. [T-01]",
                "evidence": [
                    {
                        "article_id": "T-01",
                        "title": "Settings",
                        "section": "Setup",
                        "revision": "v1",
                        "confidence": 0.9,
                    }
                ],
                "escalation_reason": None,
            }
        )

    monkeypatch.setattr(scoped_tools, "urlopen", fake_open)
    result = TechnicalTool().answer("app setup")
    request, timeout = calls[0]
    assert result.status == "answered" and timeout == 3
    assert request.full_url == "http://technical.test:8000/internal/technical/answer"
    assert request.get_header("Authorization") == "Bearer technical-secret"
    assert json.loads(request.data) == {"question": "app setup"}


def test_logistics_adapter_binds_customer_and_order(monkeypatch):
    customer_id, order_id = uuid4(), uuid4()
    monkeypatch.setenv("LOGISTICS_ORDER_READ_TOKEN", "logistics-secret")
    monkeypatch.setenv("LOGISTICS_API_URL", "http://logistics.test:8000")
    calls = []

    def fake_open(request, timeout):
        calls.append(request)
        return FakeResponse(
            {
                "order": {
                    "order_id": str(order_id),
                    "customer_id": str(customer_id),
                    "status": "in_transit",
                    "carrier": "Fixture",
                    "eta_at": None,
                    "last_updated_at": "2026-09-01T00:00:00Z",
                },
                "events": [
                    {
                        "code": "in_transit",
                        "description": "Parcel moving",
                        "occurred_at": "2026-09-01T00:00:00Z",
                    }
                ],
            }
        )

    monkeypatch.setattr(scoped_tools, "urlopen", fake_open)
    result = LogisticsTool().track(customer_id, order_id)
    assert result.order.customer_id == customer_id
    assert calls[0].full_url.endswith(f"/customers/{customer_id}/orders/{order_id}/tracking")
    assert calls[0].get_header("Authorization") == "Bearer logistics-secret"


def test_logistics_adapter_rejects_cross_customer_response(monkeypatch):
    customer_id = uuid4()
    monkeypatch.setenv("LOGISTICS_ORDER_READ_TOKEN", "logistics-secret")
    monkeypatch.setattr(
        scoped_tools,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            {
                "customer_id": str(uuid4()),
                "orders": [],
            }
        ),
    )
    with pytest.raises(ToolFailure) as failure:
        LogisticsTool().list_orders(customer_id)
    assert failure.value.code == "invalid_response"


def test_tracking_adapter_rejects_other_customer_order(monkeypatch):
    customer_id, order_id = uuid4(), uuid4()
    monkeypatch.setenv("LOGISTICS_ORDER_READ_TOKEN", "logistics-secret")
    monkeypatch.setattr(
        scoped_tools,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            {
                "order": {
                    "order_id": str(order_id),
                    "customer_id": str(uuid4()),
                    "status": "in_transit",
                    "carrier": "Fixture",
                    "eta_at": None,
                    "last_updated_at": "2026-09-01T00:00:00Z",
                },
                "events": [],
            }
        ),
    )
    with pytest.raises(ToolFailure) as failure:
        LogisticsTool().track(customer_id, order_id)
    assert failure.value.code == "invalid_response"


def test_missing_credentials_prevent_outbound_calls(monkeypatch):
    monkeypatch.delenv("LOGISTICS_ORDER_READ_TOKEN", raising=False)
    monkeypatch.setattr(scoped_tools, "urlopen", lambda *_args, **_kwargs: pytest.fail("called"))
    with pytest.raises(ToolFailure) as failure:
        LogisticsTool().list_orders(uuid4())
    assert failure.value.code == "configuration"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (HTTPError("http://test", 429, "rate limit", {}, None), "rate_limited"),
        (HTTPError("http://test", 504, "timeout", {}, None), "timeout"),
        (URLError("offline"), "unavailable"),
    ],
)
def test_upstream_failures_are_sanitized(monkeypatch, error, code):
    monkeypatch.setenv("TECHNICAL_RETRIEVAL_TOKEN", "technical-secret")

    def raise_error(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(scoped_tools, "urlopen", raise_error)
    with pytest.raises(ToolFailure) as failure:
        TechnicalTool().answer("app error")
    assert failure.value.code == code
    assert "technical-secret" not in str(failure.value)
