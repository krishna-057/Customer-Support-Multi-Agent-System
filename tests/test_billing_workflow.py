"""Billing requests pause on a customer-bound graph thread without execution."""

import os
from urllib.error import HTTPError
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from support_system import agent_api, crm_api, scoped_tools
from support_system.admin_identity import issue_admin_session
from support_system.contracts import ActionRequestRead, BillingRequestInput
from support_system.crm_models import AuditLog, Payment, RefundRequest
from support_system.customer_identity import CustomerIdentity, issue_session
from support_system.db import database_url
from support_system.fixture_ids import fixture_id
from support_system.scoped_tools import ToolFailure
from support_system.workflow_graph import build_graph
from support_system.workflow_nodes import new_state

SECRET = "test-customer-session-secret-at-least-32-bytes"


class BillingStub:
    def __init__(self, result=None):
        self.result = result
        self.calls = []
        self.results = {}
        self.decisions = []
        self.executions = []
        self.fail_execute = False

    def request(self, customer_id, body):
        self.calls.append((customer_id, body))
        if isinstance(self.result, Exception):
            raise self.result
        key = (customer_id, body.conversation_id)
        return self.results.setdefault(
            key,
            self.result
            or ActionRequestRead(
                request_id=uuid4(),
                customer_id=customer_id,
                target_id=body.target_id,
                action=body.action,
                status="pending",
            ),
        )

    def decide(self, customer_id, body, request_id, decision, admin_authorization):
        assert admin_authorization.startswith("Bearer ")
        key = (customer_id, body.conversation_id)
        current = self.results[key]
        assert current.request_id == request_id
        self.decisions.append((request_id, decision))
        status = "approved" if decision == "approve" else "rejected"
        if current.status == "executed" and decision == "approve":
            return current
        if current.status not in {"pending", status}:
            raise ToolFailure("conflict")
        self.results[key] = current.model_copy(update={"status": status})
        return self.results[key]

    def execute(self, customer_id, body, request_id):
        self.executions.append(request_id)
        if self.fail_execute:
            raise ToolFailure("unavailable")
        key = (customer_id, body.conversation_id)
        current = self.results[key]
        assert current.request_id == request_id
        assert current.status in {"approved", "executed"}
        self.results[key] = current.model_copy(update={"status": "executed"})
        return self.results[key]


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
    assert resumed["escalation_reason"] == "invalid_billing_decision"
    assert len(billing.calls) == 2
    assert billing.executions == []


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


@pytest.mark.parametrize("decision,final_status", [("approve", "executed"), ("reject", "rejected")])
def test_admin_decision_resumes_only_matching_thread(monkeypatch, decision, final_status):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ADMIN_SESSION_SECRET", SECRET)
    saver = InMemorySaver()
    billing = BillingStub()
    monkeypatch.setattr(agent_api, "BillingTool", lambda: billing)
    agent_api.app.dependency_overrides[agent_api.billing_checkpoint] = lambda: saver
    try:
        customer_id = uuid4()
        request = _request().model_dump(mode="json")
        customer_header = {"Authorization": f"Bearer {issue_session(customer_id, SECRET)}"}
        admin_header = {"Authorization": f"Bearer {issue_admin_session(uuid4(), SECRET)}"}
        with TestClient(agent_api.app) as client:
            created = client.post(
                "/v1/support/billing-requests", json=request, headers=customer_header
            )
            request_id = created.json()["request_id"]
            path = (
                f"/v1/admin/customers/{customer_id}/conversations/"
                f"{request['conversation_id']}/actions/refund/{request_id}/decision"
            )
            assert client.post(path, json={"decision": decision}).status_code == 401
            wrong = path.replace(str(customer_id), str(uuid4()))
            assert (
                client.post(wrong, json={"decision": decision}, headers=admin_header).status_code
                == 404
            )
            wrong_request = path.replace(request_id, str(uuid4()))
            wrong_conversation = path.replace(request["conversation_id"], str(uuid4()))
            wrong_action = path.replace("/actions/refund/", "/actions/cancellation/")
            for invalid_path in (wrong_request, wrong_conversation, wrong_action):
                assert (
                    client.post(
                        invalid_path, json={"decision": decision}, headers=admin_header
                    ).status_code
                    == 404
                )
            assert billing.decisions == []
            decided = client.post(path, json={"decision": decision}, headers=admin_header)
            repeated = client.post(path, json={"decision": decision}, headers=admin_header)
            conflicting = client.post(
                path,
                json={"decision": "reject" if decision == "approve" else "approve"},
                headers=admin_header,
            )
            customer_view = client.post(
                "/v1/support/billing-requests", json=request, headers=customer_header
            )
        assert created.status_code == 200
        assert decided.status_code == repeated.status_code == customer_view.status_code == 200
        assert decided.json() == repeated.json() == customer_view.json()
        assert decided.json()["status"] == final_status
        assert conflicting.status_code == 409
        assert len(billing.decisions) == 1
        assert len(billing.executions) == (1 if decision == "approve" else 0)
    finally:
        agent_api.app.dependency_overrides.clear()


def test_approved_execution_failure_keeps_thread_retryable(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ADMIN_SESSION_SECRET", SECRET)
    saver = InMemorySaver()
    billing = BillingStub()
    billing.fail_execute = True
    monkeypatch.setattr(agent_api, "BillingTool", lambda: billing)
    agent_api.app.dependency_overrides[agent_api.billing_checkpoint] = lambda: saver
    try:
        customer_id = uuid4()
        request = _request().model_dump(mode="json")
        with TestClient(agent_api.app) as client:
            created = client.post(
                "/v1/support/billing-requests",
                json=request,
                headers={"Authorization": f"Bearer {issue_session(customer_id, SECRET)}"},
            )
            path = (
                f"/v1/admin/customers/{customer_id}/conversations/"
                f"{request['conversation_id']}/actions/refund/{created.json()['request_id']}/decision"
            )
            headers = {"Authorization": f"Bearer {issue_admin_session(uuid4(), SECRET)}"}
            failed = client.post(path, json={"decision": "approve"}, headers=headers)
            assert failed.status_code == 503
            billing.fail_execute = False
            retried = client.post(path, json={"decision": "approve"}, headers=headers)
        assert retried.status_code == 200
        assert retried.json()["status"] == "executed"
        assert len(billing.executions) == 2
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


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires migrated PostgreSQL")
@pytest.mark.parametrize("decision,final_status", [("approve", "executed"), ("reject", "rejected")])
def test_postgres_gateway_resumes_across_requests(monkeypatch, decision, final_status):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    monkeypatch.setenv("ADMIN_SESSION_SECRET", SECRET)
    billing = BillingStub()
    monkeypatch.setattr(agent_api, "BillingTool", lambda: billing)
    customer_id = uuid4()
    request = _request().model_dump(mode="json")
    customer_header = {"Authorization": f"Bearer {issue_session(customer_id, SECRET)}"}
    admin_header = {"Authorization": f"Bearer {issue_admin_session(uuid4(), SECRET)}"}
    with TestClient(agent_api.app) as client:
        created = client.post("/v1/support/billing-requests", json=request, headers=customer_header)
        assert created.status_code == 200
        path = (
            f"/v1/admin/customers/{customer_id}/conversations/"
            f"{request['conversation_id']}/actions/refund/{created.json()['request_id']}/decision"
        )
        decided = client.post(path, json={"decision": decision}, headers=admin_header)
        replay = client.post(path, json={"decision": decision}, headers=admin_header)
    assert decided.status_code == replay.status_code == 200
    assert decided.json() == replay.json()
    assert decided.json()["status"] == final_status
    assert len(billing.executions) == (1 if decision == "approve" else 0)


@pytest.mark.skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", reason="requires seeded PostgreSQL")
def test_postgres_agent_crm_approval_executes_mock_refund_once(monkeypatch):
    customer_id = fixture_id("customer", 3)
    payment_id = fixture_id("payment", 3)
    conversation_id = uuid4()
    monkeypatch.setenv("CRM_API_URL", "http://crm.test:8000")

    class Response:
        def __init__(self, content):
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.content

    with TestClient(crm_api.app) as crm_client:

        def in_process_crm(request, timeout):
            assert timeout == 3
            path = request.full_url.removeprefix("http://crm.test:8000")
            response = crm_client.request(
                request.get_method(),
                path,
                content=request.data,
                headers=dict(request.header_items()),
            )
            if response.status_code >= 400:
                raise HTTPError(request.full_url, response.status_code, "CRM error", {}, None)
            return Response(response.content)

        monkeypatch.setattr(scoped_tools, "urlopen", in_process_crm)
        request = {
            "conversation_id": str(conversation_id),
            "action": "refund",
            "target_id": payment_id,
            "amount_cents": 500,
            "reason": "Duplicate service charge",
        }
        customer_header = {
            "Authorization": f"Bearer {issue_session(UUID(customer_id), os.environ['CUSTOMER_SESSION_SECRET'])}"
        }
        admin_header = {
            "Authorization": f"Bearer {issue_admin_session(uuid4(), os.environ['ADMIN_SESSION_SECRET'])}"
        }
        with TestClient(agent_api.app) as agent_client:
            created = agent_client.post(
                "/v1/support/billing-requests", json=request, headers=customer_header
            )
            assert created.status_code == 200
            assert created.json()["status"] == "approval_required"
            request_id = created.json()["request_id"]
            path = (
                f"/v1/admin/customers/{customer_id}/conversations/{conversation_id}/"
                f"actions/refund/{request_id}/decision"
            )
            decided = agent_client.post(path, json={"decision": "approve"}, headers=admin_header)
            replay = agent_client.post(path, json={"decision": "approve"}, headers=admin_header)
            assert decided.status_code == replay.status_code == 200
            assert decided.json() == replay.json()
            assert decided.json()["status"] == "executed"
    engine = create_engine(database_url())
    try:
        with Session(engine) as session:
            assert session.get(RefundRequest, request_id).status == "executed"
            assert session.get(Payment, payment_id).refunded_cents == 500
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
