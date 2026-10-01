"""Small public contracts shared by the service shells."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription_id: UUID
    plan_code: str
    status: str
    renews_at: datetime | None
    cancelled_at: datetime | None


class AccountRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID
    display_name: str
    account_status: str
    subscriptions: list[SubscriptionRead]


class InvoiceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: UUID
    subscription_id: UUID
    amount_cents: int
    currency: str
    status: str
    issued_at: datetime
    due_at: datetime


class PaymentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_id: UUID
    invoice_id: UUID
    amount_cents: int
    refundable_cents: int
    status: str
    charged_at: datetime


class BillingRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID
    invoices: list[InvoiceRead]
    payments: list[PaymentRead]


class RefundRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_id: UUID
    amount_cents: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=500)
    conversation_id: str = Field(min_length=1, max_length=100)


class CancellationRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription_id: UUID
    reason: str = Field(min_length=3, max_length=500)
    conversation_id: str = Field(min_length=1, max_length=100)


class ActionRequestRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    customer_id: UUID
    target_id: UUID
    action: Literal["refund", "cancellation"]
    status: Literal["pending", "approved", "rejected", "executed"]
