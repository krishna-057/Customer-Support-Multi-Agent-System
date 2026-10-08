"""Graph routing and authenticated gateway behavior."""

from uuid import uuid4

from fastapi.testclient import TestClient

from support_system.agent_api import app
from support_system.contracts import (
    OrderListRead,
    TechnicalAnswerRead,
    TechnicalEvidenceRead,
)
from support_system.customer_identity import CustomerIdentity, issue_session
from support_system.scoped_tools import ToolFailure
from support_system.workflow_graph import run_message

SECRET = "test-customer-session-secret-at-least-32-bytes"


class TechnicalStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def answer(self, question):
        self.calls.append(question)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class LogisticsStub:
    def __init__(self):
        self.calls = []

    def list_orders(self, customer_id):
        self.calls.append(customer_id)
        return OrderListRead(customer_id=customer_id, orders=[])

    def track(self, customer_id, order_id):
        raise AssertionError("Tracking was not requested")


def identity():
    return CustomerIdentity(uuid4(), 1000, 2000)


def test_technical_graph_requires_evidence():
    technical = TechnicalStub(
        TechnicalAnswerRead(
            status="answered",
            answer="Follow the login reset steps. [T-01]",
            evidence=[
                TechnicalEvidenceRead(
                    article_id="T-01", title="Login", section="Reset", revision="v1", confidence=0.9
                )
            ],
            escalation_reason=None,
        )
    )
    logistics = LogisticsStub()
    state = run_message(
        identity(), "My login shows error 404", technical=technical, logistics=logistics
    )
    assert state["intent"] == "technical"
    assert state["answer"].endswith("[T-01]")
    assert state["evidence"][0]["article_id"] == "T-01"
    assert technical.calls == ["My login shows error 404"]
    assert logistics.calls == []


def test_weak_evidence_and_upstream_failure_escalate():
    logistics = LogisticsStub()
    for result, reason in (
        (
            TechnicalAnswerRead(
                status="escalated", answer=None, evidence=[], escalation_reason="weak_evidence"
            ),
            "weak_evidence",
        ),
        (ToolFailure("unavailable"), "technical_unavailable"),
    ):
        state = run_message(
            identity(),
            "My app shows error 404",
            technical=TechnicalStub(result),
            logistics=logistics,
        )
        assert state["intent"] == "escalation"
        assert state["answer"] == "A support specialist will review your request."
        assert state["handoff"]["reason"] == reason
    assert logistics.calls == []


def test_fulfillment_uses_only_verified_customer_and_billing_escalates():
    customer = identity()
    technical = TechnicalStub(ToolFailure("unavailable"))
    logistics = LogisticsStub()
    state = run_message(customer, "Where is my order?", technical=technical, logistics=logistics)
    assert state["intent"] == "fulfillment"
    assert state["answer"] == "No orders were found for your account."
    assert logistics.calls == [customer.customer_id]
    state = run_message(customer, "Refund my payment", technical=technical, logistics=logistics)
    assert state["intent"] == "escalation"
    assert state["handoff"]["reason"] == "billing_workflow_unavailable"
    assert technical.calls == []
    assert logistics.calls == [customer.customer_id]


def test_gateway_requires_signed_identity_and_hides_handoff(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    customer = identity()
    logistics = LogisticsStub()
    technical = TechnicalStub(ToolFailure("unavailable"))
    monkeypatch.setattr("support_system.workflow_graph.LogisticsTool", lambda: logistics)
    monkeypatch.setattr("support_system.workflow_graph.TechnicalTool", lambda: technical)
    client = TestClient(app)
    request = {"message": "Where is my order?"}
    assert client.post("/v1/support/messages", json=request).status_code == 401
    assert (
        client.post(
            "/v1/support/messages",
            json={**request, "customer_id": str(uuid4())},
            headers={"Authorization": f"Bearer {issue_session(customer.customer_id, SECRET)}"},
        ).status_code
        == 422
    )
    response = client.post(
        "/v1/support/messages",
        json=request,
        headers={"Authorization": f"Bearer {issue_session(customer.customer_id, SECRET)}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["intent"] == "fulfillment"
    assert "handoff" not in body
    assert "customer_id" not in body
    assert logistics.calls == [customer.customer_id]
    assert technical.calls == []


def test_gateway_escalates_billing_without_service_call(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    technical = TechnicalStub(ToolFailure("unavailable"))
    logistics = LogisticsStub()
    monkeypatch.setattr("support_system.workflow_graph.LogisticsTool", lambda: logistics)
    monkeypatch.setattr("support_system.workflow_graph.TechnicalTool", lambda: technical)
    ticket_id = uuid4()

    class Tickets:
        def create(self, customer_id, conversation_id, reason):
            assert reason == "billing_workflow_unavailable"
            return type("Ticket", (), {"ticket_id": ticket_id})()

    monkeypatch.setattr("support_system.agent_api.EscalationTool", Tickets)
    customer = identity()
    response = TestClient(app).post(
        "/v1/support/messages",
        json={"message": "Cancel my subscription"},
        headers={"Authorization": f"Bearer {issue_session(customer.customer_id, SECRET)}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "escalated"
    assert response.json()["escalation_reason"] == "billing_workflow_unavailable"
    assert response.json()["ticket_id"] == str(ticket_id)
    assert technical.calls == []
    assert logistics.calls == []
