"""Escalation tickets retain only a reason code and require scoped access."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from support_system import agent_api, crm_api
from support_system.admin_identity import issue_admin_session
from support_system.contracts import TicketQueueRead
from support_system.crm_models import Base, Customer, SupportTicket
from support_system.customer_identity import issue_session
from support_system.db import get_session
from support_system.scoped_tools import ToolFailure

ADMIN_SECRET = "admin-ticket-secret-at-least-32-bytes-long"
CUSTOMER_SECRET = "customer-ticket-secret-at-least-32-bytes"


@pytest.fixture
def crm(monkeypatch):
    monkeypatch.setenv("CRM_ESCALATION_WRITE_TOKEN", "ticket-write-secret")
    monkeypatch.setenv("ADMIN_SESSION_SECRET", ADMIN_SECRET)
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    customer_ids = [uuid4(), uuid4()]
    with Session(engine) as session:
        for number, customer_id in enumerate(customer_ids):
            session.add(
                Customer(
                    id=str(customer_id),
                    email=f"ticket-{number}@example.test",
                    display_name=f"Customer {number}",
                    account_status="active",
                    created_at=datetime.now(UTC),
                )
            )
        session.commit()

    def session_override():
        with Session(engine) as session:
            yield session

    crm_api.app.dependency_overrides[get_session] = session_override
    try:
        yield TestClient(crm_api.app), engine, customer_ids
    finally:
        crm_api.app.dependency_overrides.clear()
        engine.dispose()


def test_ticket_create_is_scoped_minimal_and_replay_safe(crm):
    client, engine, customers = crm
    conversation = uuid4()
    url = f"/internal/customers/{customers[0]}/tickets"
    payload = {"conversation_id": str(conversation), "reason": "technical_unavailable"}
    assert client.post(url, json=payload).status_code == 403
    assert (
        client.post(url, json=payload, headers={"authorization": "Bearer wrong"}).status_code == 403
    )
    headers = {"authorization": "Bearer ticket-write-secret"}
    created = client.post(url, json=payload, headers=headers)
    replay = client.post(url, json=payload, headers=headers)
    changed = client.post(url, json={**payload, "reason": "human_requested"}, headers=headers)
    assert created.status_code == 201
    assert replay.status_code == 200 and replay.json() == created.json()
    assert changed.status_code == 409
    assert created.json()["summary"] == "technical_unavailable"
    assert created.json()["priority"] == "high"
    assert "message" not in created.json()
    assert "tool_result" not in created.json()
    assert (
        client.post(
            f"/internal/customers/{uuid4()}/tickets", json=payload, headers=headers
        ).status_code
        == 404
    )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SupportTicket)) == 1


def test_admin_ticket_queue_requires_session_and_pages(crm):
    client, _, customers = crm
    headers = {"authorization": "Bearer ticket-write-secret"}
    for customer in customers:
        result = client.post(
            f"/internal/customers/{customer}/tickets",
            json={"conversation_id": str(uuid4()), "reason": "human_requested"},
            headers=headers,
        )
        assert result.status_code == 201
    assert client.get("/internal/tickets").status_code == 401
    assert client.get("/internal/tickets", headers=headers).status_code == 401
    admin = {"authorization": f"Bearer {issue_admin_session(uuid4(), ADMIN_SECRET)}"}
    first = client.get("/internal/tickets?limit=1", headers=admin)
    second = client.get("/internal/tickets?limit=1&offset=1", headers=admin)
    assert first.status_code == second.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["has_more"] is True
    assert second.json()["has_more"] is False
    assert first.json()["items"][0]["ticket_id"] != second.json()["items"][0]["ticket_id"]
    assert client.get("/internal/tickets?limit=101", headers=admin).status_code == 422


def test_agent_escalation_fails_closed_if_ticket_write_fails(monkeypatch):
    monkeypatch.setenv("CUSTOMER_SESSION_SECRET", CUSTOMER_SECRET)

    class Tickets:
        def create(self, *_args):
            raise ToolFailure("unavailable")

    monkeypatch.setattr(agent_api, "EscalationTool", Tickets)
    headers = {"authorization": f"Bearer {issue_session(uuid4(), CUSTOMER_SECRET)}"}
    response = TestClient(agent_api.app).post(
        "/v1/support/messages", json={"message": "I need a human"}, headers=headers
    )
    stream = TestClient(agent_api.app).post(
        "/v1/support/messages/stream", json={"message": "I need a human"}, headers=headers
    )
    assert response.status_code == 503
    assert "event: error" in stream.text
    assert "event: completed" not in stream.text


def test_agent_ticket_queue_is_admin_only(monkeypatch):
    monkeypatch.setenv("ADMIN_SESSION_SECRET", ADMIN_SECRET)
    calls = []

    class Tickets:
        def list_open(self, authorization, *, offset):
            calls.append((authorization, offset))
            return TicketQueueRead(items=[], offset=offset, has_more=False)

    monkeypatch.setattr(agent_api, "EscalationTool", Tickets)
    client = TestClient(agent_api.app)
    assert client.get("/v1/admin/tickets").status_code == 401
    admin = {"authorization": f"Bearer {issue_admin_session(uuid4(), ADMIN_SECRET)}"}
    response = client.get("/v1/admin/tickets?offset=3", headers=admin)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"items": [], "offset": 3, "has_more": False}
    assert calls == [(admin["authorization"], 3)]
