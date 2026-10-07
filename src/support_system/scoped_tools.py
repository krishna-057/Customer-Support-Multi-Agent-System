"""Narrow HTTP adapters; graph nodes never receive privileged credentials."""

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import ValidationError

from support_system.contracts import (
    ActionRequestRead,
    BillingRequestInput,
    OrderListRead,
    ReviewQueueRead,
    TechnicalAnswerRead,
    TrackingRead,
)

MAX_RESPONSE_BYTES = 65536


@dataclass
class ToolFailure(Exception):
    code: str
    status_code: int | None = None


def _call(
    url: str,
    token: str | None,
    *,
    question: str | None = None,
    body: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    if not token:
        raise ToolFailure("configuration")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    payload = None
    if question is not None or body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps({"question": question} if question is not None else body).encode(
            "utf-8"
        )
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    request = Request(url, data=payload, headers=headers, method="POST" if payload else "GET")
    try:
        with urlopen(request, timeout=3) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        code = {
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            429: "rate_limited",
            504: "timeout",
        }.get(exc.code, "unavailable")
        raise ToolFailure(code, exc.code) from None
    except (URLError, TimeoutError, OSError):
        raise ToolFailure("unavailable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ToolFailure("invalid_response")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        raise ToolFailure("invalid_response") from None
    if not isinstance(value, dict):
        raise ToolFailure("invalid_response")
    return value


class TechnicalTool:
    def answer(self, question: str) -> TechnicalAnswerRead:
        base = os.getenv("TECHNICAL_API_URL", "http://127.0.0.1:8000")
        value = _call(
            f"{base.rstrip('/')}/internal/technical/answer",
            os.getenv("TECHNICAL_RETRIEVAL_TOKEN"),
            question=question,
        )
        try:
            return TechnicalAnswerRead.model_validate(value)
        except ValidationError:
            raise ToolFailure("invalid_response") from None


class LogisticsTool:
    def list_orders(self, customer_id: UUID) -> OrderListRead:
        base = os.getenv("LOGISTICS_API_URL", "http://127.0.0.1:8002")
        value = _call(
            f"{base.rstrip('/')}/internal/customers/{customer_id}/orders",
            os.getenv("LOGISTICS_ORDER_READ_TOKEN"),
        )
        try:
            result = OrderListRead.model_validate(value)
        except ValidationError:
            raise ToolFailure("invalid_response") from None
        if result.customer_id != customer_id or any(
            item.customer_id != customer_id for item in result.orders
        ):
            raise ToolFailure("invalid_response")
        return result

    def track(self, customer_id: UUID, order_id: UUID) -> TrackingRead:
        base = os.getenv("LOGISTICS_API_URL", "http://127.0.0.1:8002")
        value = _call(
            f"{base.rstrip('/')}/internal/customers/{customer_id}/orders/{order_id}/tracking",
            os.getenv("LOGISTICS_ORDER_READ_TOKEN"),
        )
        try:
            result = TrackingRead.model_validate(value)
        except ValidationError:
            raise ToolFailure("invalid_response") from None
        if result.order.customer_id != customer_id or result.order.order_id != order_id:
            raise ToolFailure("invalid_response")
        return result


class BillingTool:
    def list_review_actions(
        self, admin_authorization: str, *, limit: int = 50, offset: int = 0
    ) -> ReviewQueueRead:
        base = os.getenv("CRM_API_URL", "http://127.0.0.1:8001")
        value = _call(
            f"{base.rstrip('/')}/internal/action-requests/review?limit={limit}&offset={offset}",
            admin_authorization.removeprefix("Bearer "),
        )
        try:
            return ReviewQueueRead.model_validate(value)
        except ValidationError:
            raise ToolFailure("invalid_response") from None

    def request(self, customer_id: UUID, body: BillingRequestInput) -> ActionRequestRead:
        base = os.getenv("CRM_API_URL", "http://127.0.0.1:8001")
        path = "refund-requests" if body.action == "refund" else "cancellation-requests"
        payload = {
            "payment_id" if body.action == "refund" else "subscription_id": str(body.target_id),
            "reason": body.reason,
            "conversation_id": str(body.conversation_id),
        }
        if body.action == "refund":
            payload["amount_cents"] = body.amount_cents
        value = _call(
            f"{base.rstrip('/')}/internal/customers/{customer_id}/{path}",
            os.getenv("CRM_BILLING_REQUEST_TOKEN"),
            body=payload,
            idempotency_key=f"billing:{body.conversation_id}",
        )
        try:
            result = ActionRequestRead.model_validate(value)
        except ValidationError:
            raise ToolFailure("invalid_response") from None
        if (
            result.customer_id != customer_id
            or result.target_id != body.target_id
            or result.action != body.action
        ):
            raise ToolFailure("invalid_response")
        return result

    def decide(
        self,
        customer_id: UUID,
        body: BillingRequestInput,
        request_id: UUID,
        decision: str,
        admin_authorization: str,
    ) -> ActionRequestRead:
        base = os.getenv("CRM_API_URL", "http://127.0.0.1:8001")
        value = _call(
            f"{base.rstrip('/')}/internal/action-requests/{body.action}/{request_id}/decision",
            admin_authorization.removeprefix("Bearer "),
            body={"decision": decision},
        )
        return self._validate_action(value, customer_id, body, request_id)

    def execute(
        self, customer_id: UUID, body: BillingRequestInput, request_id: UUID
    ) -> ActionRequestRead:
        base = os.getenv("CRM_API_URL", "http://127.0.0.1:8001")
        value = _call(
            f"{base.rstrip('/')}/internal/customers/{customer_id}/action-requests/"
            f"{body.action}/{request_id}/execute",
            os.getenv("CRM_BILLING_EXECUTE_TOKEN"),
            body={"conversation_id": str(body.conversation_id)},
        )
        result = self._validate_action(value, customer_id, body, request_id)
        if result.status != "executed":
            raise ToolFailure("invalid_response")
        return result

    @staticmethod
    def _validate_action(
        value: dict, customer_id: UUID, body: BillingRequestInput, request_id: UUID
    ) -> ActionRequestRead:
        try:
            result = ActionRequestRead.model_validate(value)
        except ValidationError:
            raise ToolFailure("invalid_response") from None
        if (
            result.customer_id != customer_id
            or result.target_id != body.target_id
            or result.action != body.action
            or result.request_id != request_id
        ):
            raise ToolFailure("invalid_response")
        return result
