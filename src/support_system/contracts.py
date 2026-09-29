"""Small public contracts shared by the service shells."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ServiceName(StrEnum):
    AGENT = "agent-api"
    CRM = "crm-api"
    LOGISTICS = "logistics-api"


class HealthStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: ServiceName
    status: HealthStatus


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
