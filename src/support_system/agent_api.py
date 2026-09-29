"""Customer-facing API shell; conversation routes arrive after backend gates."""

from fastapi import FastAPI

from support_system.contracts import HealthResponse, HealthStatus, ServiceName

app = FastAPI(title="Support Agent API", version="0.1.0")


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def live() -> HealthResponse:
    return HealthResponse(service=ServiceName.AGENT, status=HealthStatus.OK)


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def ready() -> HealthResponse:
    return HealthResponse(service=ServiceName.AGENT, status=HealthStatus.OK)
