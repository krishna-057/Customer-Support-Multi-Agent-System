"""Logistics API shell; order reads arrive after contracts are defined."""

from fastapi import FastAPI

from support_system.contracts import HealthResponse, HealthStatus, ServiceName

app = FastAPI(title="Support Logistics API", version="0.1.0")


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def live() -> HealthResponse:
    return HealthResponse(service=ServiceName.LOGISTICS, status=HealthStatus.OK)


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def ready() -> HealthResponse:
    return HealthResponse(service=ServiceName.LOGISTICS, status=HealthStatus.OK)
