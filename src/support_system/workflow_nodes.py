"""Deterministic supervisor and scoped nodes for the future LangGraph assembly."""

import re
from typing import Literal, Protocol, TypedDict
from uuid import UUID

from langgraph.types import interrupt

from support_system.contracts import (
    ActionRequestRead,
    BillingRequestInput,
    OrderListRead,
    TechnicalAnswerRead,
    TrackingRead,
)
from support_system.customer_identity import CustomerIdentity
from support_system.scoped_tools import ToolFailure

Intent = Literal["technical", "fulfillment", "billing", "escalation"]


class SupportState(TypedDict, total=False):
    customer_id: str
    message: str
    order_id: str | None
    intent: Intent
    answer: str | None
    evidence: list[dict]
    tracking: dict | None
    escalation_reason: str | None
    handoff: dict
    billing_action: dict
    billing_result: dict


class TechnicalPort(Protocol):
    def answer(self, question: str) -> TechnicalAnswerRead: ...


class LogisticsPort(Protocol):
    def list_orders(self, customer_id: UUID) -> OrderListRead: ...

    def track(self, customer_id: UUID, order_id: UUID) -> TrackingRead: ...


class BillingPort(Protocol):
    def request(self, customer_id: UUID, body: BillingRequestInput) -> ActionRequestRead: ...

    def execute(
        self, customer_id: UUID, body: BillingRequestInput, request_id: UUID
    ) -> ActionRequestRead: ...


TECHNICAL_TERMS = frozenset(
    {"404", "app", "bug", "error", "login", "password", "reset", "setup", "troubleshoot"}
)
FULFILLMENT_TERMS = frozenset(
    {"delivery", "eta", "order", "orders", "parcel", "shipment", "tracking"}
)
BILLING_TERMS = frozenset(
    {"billing", "cancel", "charge", "charged", "invoice", "payment", "refund", "subscription"}
)
HUMAN_TERMS = frozenset({"human", "manager", "representative"})


def new_state(
    identity: CustomerIdentity, message: str, *, order_id: UUID | None = None
) -> SupportState:
    """The customer ID comes only from verified session claims, never request JSON."""
    return {
        "customer_id": str(identity.customer_id),
        "message": message,
        "order_id": str(order_id) if order_id else None,
    }


def supervisor(state: SupportState) -> SupportState:
    words = set(re.findall(r"[a-z0-9]+", state.get("message", "").lower()))
    if not words:
        return {"intent": "escalation", "escalation_reason": "empty_request"}
    if words & HUMAN_TERMS or "speak to an agent" in state["message"].lower():
        return {"intent": "escalation", "escalation_reason": "human_requested"}
    categories = [
        name
        for name, terms in (
            ("technical", TECHNICAL_TERMS),
            ("fulfillment", FULFILLMENT_TERMS),
            ("billing", BILLING_TERMS),
        )
        if words & terms
    ]
    if len(categories) > 1:
        return {"intent": "escalation", "escalation_reason": "ambiguous_intent"}
    if categories == ["billing"] and not state.get("billing_action"):
        return {"intent": "escalation", "escalation_reason": "billing_workflow_unavailable"}
    if categories:
        return {"intent": categories[0]}
    return {"intent": "escalation", "escalation_reason": "unclassified_request"}


def route_after_supervisor(state: SupportState) -> Intent:
    return state.get("intent", "escalation")


def _customer(state: SupportState) -> UUID | None:
    try:
        key = state.get("customer_id")
        return UUID(key) if key else None
    except ValueError:
        return None


def technical_node(state: SupportState, tool: TechnicalPort) -> SupportState:
    if _customer(state) is None:
        return {"intent": "escalation", "escalation_reason": "invalid_customer_identity"}
    try:
        result = tool.answer(state["message"])
    except ToolFailure as exc:
        return {"intent": "escalation", "escalation_reason": f"technical_{exc.code}"}
    if result.status != "answered" or not result.answer or not result.evidence:
        return {
            "intent": "escalation",
            "escalation_reason": result.escalation_reason or "weak_evidence",
        }
    return {
        "answer": result.answer,
        "evidence": [item.model_dump(mode="json") for item in result.evidence],
    }


def fulfillment_node(state: SupportState, tool: LogisticsPort) -> SupportState:
    customer_id = _customer(state)
    if customer_id is None:
        return {"intent": "escalation", "escalation_reason": "invalid_customer_identity"}
    try:
        if state.get("order_id"):
            order_id = UUID(state["order_id"])
            tracking = tool.track(customer_id, order_id)
            last_event = tracking.events[-1] if tracking.events else None
            message = f"Your order is {tracking.order.status.replace('_', ' ')}."
            if last_event:
                message += f" Latest update: {last_event.description}."
            return {"answer": message, "tracking": tracking.model_dump(mode="json")}
        orders = tool.list_orders(customer_id)
        if not orders.orders:
            return {"answer": "No orders were found for your account.", "tracking": None}
        return {
            "answer": f"I found {len(orders.orders)} order(s) on your account.",
            "tracking": orders.model_dump(mode="json"),
        }
    except ValueError:
        return {"intent": "escalation", "escalation_reason": "invalid_order_id"}
    except ToolFailure as exc:
        return {"intent": "escalation", "escalation_reason": f"logistics_{exc.code}"}


def billing_node(state: SupportState, tool: BillingPort) -> SupportState:
    customer_id = _customer(state)
    if customer_id is None:
        return {"intent": "escalation", "escalation_reason": "invalid_customer_identity"}
    try:
        body = BillingRequestInput.model_validate(state["billing_action"])
        result = tool.request(customer_id, body)
    except (KeyError, ValueError, ToolFailure) as exc:
        reason = f"billing_{exc.code}" if isinstance(exc, ToolFailure) else "invalid_billing_action"
        return {"intent": "escalation", "escalation_reason": reason}
    decision = interrupt({"request_id": str(result.request_id), "action": result.action})
    if (
        not isinstance(decision, dict)
        or decision.get("request_id") != str(result.request_id)
        or decision.get("decision") not in {"approve", "reject"}
    ):
        return {"intent": "escalation", "escalation_reason": "invalid_billing_decision"}
    if decision["decision"] == "reject":
        if result.status != "rejected":
            return {"intent": "escalation", "escalation_reason": "unverified_billing_decision"}
        return {
            "answer": "Your request was not approved.",
            "billing_result": {"request_id": str(result.request_id), "status": "rejected"},
        }
    if result.status not in {"approved", "executed"}:
        return {"intent": "escalation", "escalation_reason": "unverified_billing_decision"}
    executed = tool.execute(customer_id, body, result.request_id)
    return {
        "answer": "Your request was completed.",
        "billing_result": {"request_id": str(executed.request_id), "status": "executed"},
    }


def escalation_node(state: SupportState) -> SupportState:
    reason = state.get("escalation_reason") or "human_review_required"
    return {
        "intent": "escalation",
        "answer": "A support specialist will review your request.",
        "handoff": {
            "customer_id": state.get("customer_id"),
            "reason": reason,
            "last_user_message": state.get("message", "")[:500],
            "tool_result": state.get("tracking") or state.get("evidence"),
        },
    }
