"""Customer-visible stream contract; privileged graph state stays server-side."""

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from support_system.agent_api import app
from support_system.contracts import TechnicalAnswerRead, TechnicalEvidenceRead
from support_system.customer_identity import issue_session
from support_system.scoped_tools import ToolFailure

SECRET = "test-customer-session-secret-at-least-32-bytes"


def _events(response):
    events = []
    for frame in response.text.strip().split("\n\n"):
        name, data = frame.split("\n", 1)
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


def _headers(customer_id):
    return {"Authorization": f"Bearer {issue_session(customer_id, SECRET)}"}


def test_stream_requires_customer_session(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    client = TestClient(app)
    assert (
        client.post(
            "/v1/support/messages/stream", json={"message": "My app has an error"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/support/messages/stream",
            json={"message": "My app has an error", "customer_id": str(uuid4())},
            headers=_headers(uuid4()),
        ).status_code
        == 422
    )


def test_stream_reports_grounded_progress_without_identity_or_handoff(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)
    evidence = TechnicalEvidenceRead(
        article_id="T-01", title="Login", section="Reset", revision="v1", confidence=0.9
    )

    class Technical:
        def answer(self, question):
            return TechnicalAnswerRead(
                status="answered",
                answer="Reset your password. [T-01]",
                evidence=[evidence],
                escalation_reason=None,
            )

    monkeypatch.setattr("support_system.workflow_graph.TechnicalTool", Technical)
    customer_id = uuid4()
    response = TestClient(app).post(
        "/v1/support/messages/stream",
        json={"message": "My login has an error"},
        headers=_headers(customer_id),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    events = _events(response)
    assert [name for name, _ in events] == [
        "routing",
        "tool_started",
        "retrieval",
        "tool_finished",
        "completed",
    ]
    assert events[0][1] == {"intent": "technical"}
    assert events[2][1]["evidence"][0]["article_id"] == "T-01"
    assert events[-1][1]["answer"] == "Reset your password. [T-01]"
    assert str(customer_id) not in response.text
    assert "handoff" not in response.text


def test_stream_escalates_tool_failure_without_leaking_handoff(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)

    class Technical:
        def answer(self, question):
            raise ToolFailure("unavailable")

    monkeypatch.setattr("support_system.workflow_graph.TechnicalTool", Technical)
    response = TestClient(app).post(
        "/v1/support/messages/stream",
        json={"message": "My login has an error"},
        headers=_headers(uuid4()),
    )
    events = _events(response)
    assert [name for name, _ in events] == [
        "routing",
        "tool_started",
        "tool_finished",
        "escalated",
        "completed",
    ]
    assert events[-1][1]["status"] == "escalated"
    assert events[-1][1]["escalation_reason"] == "technical_unavailable"
    assert "handoff" not in response.text


def test_stream_sanitizes_unexpected_backend_failure(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", SECRET)

    def broken_stream(*args, **kwargs):
        raise RuntimeError("database password is secret-value")
        yield

    monkeypatch.setattr("support_system.agent_api.stream_message", broken_stream)
    response = TestClient(app).post(
        "/v1/support/messages/stream",
        json={"message": "My login has an error"},
        headers=_headers(uuid4()),
    )
    assert _events(response) == [("error", {"message": "Support response unavailable"})]
    assert "secret-value" not in response.text
