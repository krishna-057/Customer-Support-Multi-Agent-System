"""Supervisor and node behavior without external model or service calls."""

from uuid import UUID, uuid4

import pytest

from support_system.contracts import (
    OrderListRead,
    TechnicalAnswerRead,
    TechnicalEvidenceRead,
    TrackingRead,
)
from support_system.customer_identity import CustomerIdentity
from support_system.logistics_data import EVENTS, ORDERS
from support_system.scoped_tools import ToolFailure
from support_system.workflow_nodes import (
    escalation_node,
    fulfillment_node,
    new_state,
    route_after_supervisor,
    supervisor,
    technical_node,
)


class FakeTechnical:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def answer(self, question):
        self.calls.append(question)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeLogistics:
    def __init__(self, customer_id):
        self.customer_id = customer_id
        self.calls = []

    def list_orders(self, customer_id):
        self.calls.append(("list", customer_id))
        assert customer_id == self.customer_id
        return OrderListRead.model_validate(
            {
                "customer_id": str(customer_id),
                "orders": [
                    item for item in ORDERS.values() if item["customer_id"] == str(customer_id)
                ],
            }
        )

    def track(self, customer_id, order_id):
        self.calls.append(("track", customer_id, order_id))
        assert customer_id == self.customer_id
        return TrackingRead.model_validate(
            {"order": ORDERS[str(order_id)], "events": EVENTS[str(order_id)]}
        )


@pytest.mark.parametrize(
    ("message", "intent", "reason"),
    [
        ("My app shows error 404", "technical", None),
        ("Where is my order?", "fulfillment", None),
        ("Refund this payment", "escalation", "billing_workflow_unavailable"),
        ("My app error and order is late", "escalation", "ambiguous_intent"),
        ("I want a human manager", "escalation", "human_requested"),
        ("Hello", "escalation", "unclassified_request"),
    ],
)
def test_supervisor_routes_conservatively(message, intent, reason):
    state = {"message": message}
    update = supervisor(state)
    assert route_after_supervisor(update) == intent
    assert update.get("escalation_reason") == reason


def test_identity_binding_precedes_tool_use():
    customer_id = uuid4()
    identity = CustomerIdentity(customer_id, 1000, 2000)
    state = new_state(identity, "Where is my order?")
    assert state["customer_id"] == str(customer_id)
    logistics = FakeLogistics(customer_id)
    result = fulfillment_node(state, logistics)
    assert result["answer"] == "No orders were found for your account."
    assert logistics.calls == [("list", customer_id)]


def test_fulfillment_tracks_only_bound_customer_order():
    customer_id = next(iter(ORDERS.values()))["customer_id"]
    order_id = next(iter(ORDERS))
    identity = CustomerIdentity(UUID(customer_id), 1000, 2000)
    state = new_state(identity, "Track my order", order_id=UUID(order_id))
    logistics = FakeLogistics(identity.customer_id)
    result = fulfillment_node(state, logistics)
    assert "in transit" in result["answer"]
    assert logistics.calls == [("track", identity.customer_id, UUID(order_id))]


def test_technical_node_requires_evidence_and_escalates_weak_results():
    state = {"customer_id": str(uuid4()), "message": "app error 404"}
    evidence = TechnicalEvidenceRead(
        article_id="T-01", title="404", section="Login", revision="v1", confidence=0.9
    )
    good = FakeTechnical(
        TechnicalAnswerRead(
            status="answered",
            answer="Use help. [T-01]",
            evidence=[evidence],
            escalation_reason=None,
        )
    )
    result = technical_node(state, good)
    assert result["answer"] == "Use help. [T-01]"
    assert result["evidence"][0]["article_id"] == "T-01"
    weak = FakeTechnical(
        TechnicalAnswerRead(
            status="escalated",
            answer=None,
            evidence=[],
            escalation_reason="low_retrieval_confidence",
        )
    )
    assert technical_node(state, weak)["escalation_reason"] == "low_retrieval_confidence"


def test_nodes_fail_closed_without_identity_or_on_service_failure():
    technical = FakeTechnical(ToolFailure("unavailable"))
    assert (
        technical_node({"message": "app error"}, technical)["escalation_reason"]
        == "invalid_customer_identity"
    )
    assert technical.calls == []
    state = {"customer_id": str(uuid4()), "message": "app error"}
    assert technical_node(state, technical)["escalation_reason"] == "technical_unavailable"
    logistics = FakeLogistics(uuid4())
    assert (
        fulfillment_node({"message": "order"}, logistics)["escalation_reason"]
        == "invalid_customer_identity"
    )
    assert logistics.calls == []


def test_escalation_handoff_has_reason_and_context():
    state = {
        "customer_id": str(uuid4()),
        "message": "Please refund",
        "escalation_reason": "billing_workflow_unavailable",
    }
    result = escalation_node(state)
    assert result["handoff"]["reason"] == "billing_workflow_unavailable"
    assert result["handoff"]["last_user_message"] == "Please refund"
