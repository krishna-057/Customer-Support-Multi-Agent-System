"""CRM API shell; business reads and writes require later backend gates."""

import os

import psycopg
from fastapi import FastAPI, Response, status

from support_system.contracts import HealthResponse, HealthStatus, ServiceName

app = FastAPI(title="Support CRM API", version="0.1.0")


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def live() -> HealthResponse:
    return HealthResponse(service=ServiceName.CRM, status=HealthStatus.OK)


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def ready(response: Response) -> HealthResponse:
    database_password = os.getenv("DB_PASSWORD")
    if not database_password:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(service=ServiceName.CRM, status=HealthStatus.UNAVAILABLE)

    try:
        with psycopg.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "support"),
            user=os.getenv("DB_USER", "support"),
            password=database_password,
            connect_timeout=2,
        ) as connection:
            connection.execute("SELECT 1")
    except psycopg.Error:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(service=ServiceName.CRM, status=HealthStatus.UNAVAILABLE)

    return HealthResponse(service=ServiceName.CRM, status=HealthStatus.OK)
