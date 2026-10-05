"""Billing requests pause on a customer-bound graph thread without execution."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command

from support_system import agent_api
from support_system.contracts import ActionRequestRead, BillingRequestInput
from support_system.customer_identity import CustomerIdentity, issue_session
from support_system.db import database_url
from support_system.scoped_tools import ToolFailure
from support_system.workflow_graph import build_graph
from support_system.workflow_nodes import new_state

SECRET = "test-customer-session-secret-at-least-32-bytes"


class BillingStub:
    def __init__(self, result=None):
        self.result = result
        self.calls = []

    def request(self, customer_id, body):
        self.calls.append((customer_id, body))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result or ActionRequestRead(
            request_id=uuid4(),
            customer_id=customer_id,
            target_id=body.target_id,
            action=body.action,
            status="pending",
        )


def _request(action="refund"):
    return BillingRequestInput(
        conversation_id=uuid4(),
        action=action,
        target_id=uuid4(),
        amount_cents=1000 if action == "refund" else None,
        reason="Customer requested this action",
    )


def test_graph_interrupt_is_customer_bound_and_does_not_execute():
    customer_id = uuid4()
    identity = CustomerIdentity(customer_id, 1000, 2000)
    request = _request()
    billing = BillingStub()
    saver = InMemorySaver()
    graph = build_graph(None, None, billing=billing, checkpointer=saver)
    config = {"configurable": {"thread_id": f"{customer_id}:{request.conversation_id}"}}
    state = new_state(identity, "refund payment")
    state["billing_action"] = request.model_dump(mode="json")

    result = graph.invoke(state, config=config)
    assert result["__interrupt__"][0].value["action"] == "refund"
    assert len(billing.calls) == 1
    assert billing.calls[0][0] == customer_id
    snapshot = graph.get_state(config)
    assert snapshot.values["customer_id"] == str(customer_id)
    assert snapshot.values["billing_action"] == state["billing_action"]
    assert snapshot.tasks[0].interrupts[0].value["request_id"]

    resumed = graph.invoke(Command(resume={"decision": "approve"}), config=config)
    assert resumed["intent"] == "escalation"
    assert resumed["escalation_reason"] == "billing_resume_unavailable"
    assert len(billing.calls) == 2


def test_billing_tool_failure_escalates_without_interrupt():
    customer_id = uuid4()
    identity = CustomerIdentity(customer_id, 1000, 2000)
    request = _request("cancellation")
    billing = BillingStub(ToolFailure("unavailable"))
    graph = build_graph(None, None, billing=billing, checkpointer=InMemorySaver())
    state = new_state(identity, "cancel subscription")
    state["billing_action"] = request.model_dump(mode="json")
    result = graph.invoke(
        state, config={"configurable": {"thread_id": str(request.conversation_id)}}
    )
    assert result["intent"] == "escalation"
    assert result["escalation_reason"] == "billing_unavailable"
    assert "__interrupt__" not in result


def test_gateway_requires_identity_and_replays_only_same_payload(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    saver = InMemorySaver()
    billing = BillingStub()
    monkeypatch.setattr(agent_api, "BillingTool", lambda: billing)
    agent_api.app.dependency_overrides[agent_api.billing_checkpoint] = lambda: saver
    try:
        customer_id = uuid4()
        headers = {"Authorization": f"Bearer {issue_session(customer_id, SECRET)}"}
        request = _request().model_dump(mode="json")
        with TestClient(agent_api.app) as client:
            assert client.post("/v1/support/billing-requests", json=request).status_code == 401
            first = client.post("/v1/support/billing-requests", json=request, headers=headers)
            replay = client.post("/v1/support/billing-requests", json=request, headers=headers)
            conflict = client.post(
                "/v1/support/billing-requests",
                json={**request, "amount_cents": 2000},
                headers=headers,
            )
            other = client.post(
                "/v1/support/billing-requests",
                json=request,
                headers={"Authorization": f"Bearer {issue_session(uuid4(), SECRET)}"},
            )
        assert first.status_code == replay.status_code == other.status_code == 200
        assert first.json() == replay.json()
        assert first.json()["status"] == "approval_required"
        assert first.json()["request_id"]
        assert conflict.status_code == 409
        assert len(billing.calls) == 2
        assert billing.calls[0][0] == customer_id
        assert billing.calls[1][0] != customer_id
    finally:
        agent_api.app.dependency_overrides.clear()


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires migrated PostgreSQL")
def test_interrupted_thread_survives_new_postgres_connection():
    customer_id = uuid4()
    request = _request()
    identity = CustomerIdentity(customer_id, 1000, 2000)
    config = {"configurable": {"thread_id": f"{customer_id}:{request.conversation_id}"}}
    state = new_state(identity, "refund payment")
    state["billing_action"] = request.model_dump(mode="json")
    dsn = database_url().set(drivername="postgresql").render_as_string(hide_password=False)
    with PostgresSaver.from_conn_string(dsn) as saver:
        saver.setup()
        graph = build_graph(None, None, billing=BillingStub(), checkpointer=saver)
        result = graph.invoke(state, config=config)
        request_id = result["__interrupt__"][0].value["request_id"]
    with PostgresSaver.from_conn_string(dsn) as saver:
        graph = build_graph(None, None, billing=BillingStub(), checkpointer=saver)
        snapshot = graph.get_state(config)
    assert snapshot.values["customer_id"] == str(customer_id)
    assert snapshot.tasks[0].interrupts[0].value["request_id"] == request_id
