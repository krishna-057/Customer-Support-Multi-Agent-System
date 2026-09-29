from unittest.mock import MagicMock, patch

import psycopg
import pytest
from fastapi.testclient import TestClient

from support_system import agent_api, crm_api, logistics_api


@pytest.mark.parametrize(
    ("app", "service"),
    [
        (agent_api.app, "agent-api"),
        (crm_api.app, "crm-api"),
        (logistics_api.app, "logistics-api"),
    ],
)
def test_liveness_contract(app, service):
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"service": service, "status": "ok"}


@pytest.mark.parametrize("app", [agent_api.app, logistics_api.app])
def test_stateless_readiness(app):
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_crm_readiness_fails_closed_without_database(monkeypatch):
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    response = TestClient(crm_api.app).get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"service": "crm-api", "status": "unavailable"}


def test_crm_readiness_hides_connection_details(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "secret")
    with patch("support_system.crm_api.psycopg.connect", side_effect=psycopg.OperationalError("secret")):
        response = TestClient(crm_api.app).get("/health/ready")
    assert response.status_code == 503
    assert "secret" not in response.text


def test_crm_readiness_checks_database(monkeypatch):
    monkeypatch.setenv("DB_PASSWORD", "local-test-password")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    with patch("support_system.crm_api.psycopg.connect", return_value=connection):
        response = TestClient(crm_api.app).get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"service": "crm-api", "status": "ok"}
    connection.execute.assert_called_once_with("SELECT 1")


def test_no_business_routes_open_before_authorization_gate():
    for app in (agent_api.app, crm_api.app, logistics_api.app):
        with TestClient(app) as client:
            assert client.post("/refunds", json={}).status_code == 404
            assert client.post("/chat", json={}).status_code == 404
