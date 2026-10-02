"""Scoped, read-only synthetic order and tracking service."""

import hmac
import os
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException

from support_system.contracts import (
    HealthResponse,
    HealthStatus,
    OrderListRead,
    ServiceName,
    TrackingRead,
)
from support_system.logistics_data import EVENTS, FAILURES, FIRST_CUSTOMER, ORDERS

app = FastAPI(title="Support Logistics API", version="0.1.0")


def require_order_scope(authorization: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("LOGISTICS_ORDER_READ_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Logistics unavailable")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not authorization or not authorization.startswith("Bearer ") or not hmac.compare_digest(
        supplied, expected
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get(
    "/internal/customers/{customer_id}/orders",
    response_model=OrderListRead,
    dependencies=[Depends(require_order_scope)],
)
def orders(customer_id: UUID) -> dict:
    key = str(customer_id)
    return {
        "customer_id": key,
        "orders": [item for item in ORDERS.values() if item["customer_id"] == key],
    }


@app.get(
    "/internal/customers/{customer_id}/orders/{order_id}/tracking",
    response_model=TrackingRead,
    dependencies=[Depends(require_order_scope)],
)
def tracking(customer_id: UUID, order_id: UUID) -> dict:
    key = str(order_id)
    order = ORDERS.get(key)
    if order is None or order["customer_id"] != str(customer_id):
        # A caller cannot discover whether another customer owns an order.
        if str(customer_id) == FIRST_CUSTOMER and key in FAILURES:
            code, message, headers = FAILURES[key]
            raise HTTPException(status_code=code, detail=message, headers=headers)
        raise HTTPException(status_code=404, detail="Order not found")
    return {"order": order, "events": EVENTS[key]}


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def live() -> HealthResponse:
    return HealthResponse(service=ServiceName.LOGISTICS, status=HealthStatus.OK)


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def ready() -> HealthResponse:
    return HealthResponse(service=ServiceName.LOGISTICS, status=HealthStatus.OK)
