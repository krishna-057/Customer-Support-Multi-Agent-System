"""Agent API shell and guarded technical evidence endpoint."""

import hmac
import json
import os
from collections.abc import Iterator
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg import Error as PostgresError
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from support_system.admin_identity import AdminIdentity, require_admin_identity
from support_system.contracts import (
    ActionDecisionInput,
    BillingRequestInput,
    BillingRequestRead,
    CustomerMessageInput,
    CustomerMessageRead,
    HealthResponse,
    HealthStatus,
    ReviewQueueRead,
    ServiceName,
    TechnicalAnswerRead,
    TechnicalQuestion,
    TicketQueueRead,
)
from support_system.crm_models import SupportArticle
from support_system.customer_identity import CustomerIdentity, require_customer_identity
from support_system.db import database_url
from support_system.scoped_tools import (
    BillingTool,
    EscalationTool,
    LogisticsTool,
    TechnicalTool,
    ToolFailure,
)
from support_system.support_knowledge import answer_technical
from support_system.workflow_graph import build_graph, run_message, stream_message
from support_system.workflow_nodes import new_state

app = FastAPI(title="Support Agent API", version="0.1.0")


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def live() -> HealthResponse:
    return HealthResponse(service=ServiceName.AGENT, status=HealthStatus.OK)


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def ready() -> HealthResponse:
    try:
        engine = create_engine(database_url(), connect_args={"connect_timeout": 2})
        try:
            with Session(engine) as session:
                if session.scalar(select(func.count()).select_from(SupportArticle)):
                    return HealthResponse(service=ServiceName.AGENT, status=HealthStatus.OK)
        finally:
            engine.dispose()
    except (RuntimeError, ValueError, OSError, SQLAlchemyError):
        pass
    raise HTTPException(status_code=503, detail="Knowledge base unavailable")


def knowledge_scope(authorization: Annotated[str | None, Header()] = None) -> None:
    token = os.getenv("TECHNICAL_RETRIEVAL_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="Knowledge base unavailable")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if (
        not authorization
        or not authorization.startswith("Bearer ")
        or not hmac.compare_digest(supplied, token)
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


def knowledge_session() -> Iterator[Session]:
    try:
        engine = create_engine(database_url(), connect_args={"connect_timeout": 2})
        with Session(engine) as session:
            yield session
    except (RuntimeError, ValueError, OSError, SQLAlchemyError) as exc:
        raise HTTPException(status_code=503, detail="Knowledge base unavailable") from exc
    finally:
        if "engine" in locals():
            engine.dispose()


@app.post(
    "/internal/technical/answer",
    dependencies=[Depends(knowledge_scope)],
    response_model=TechnicalAnswerRead,
)
def technical_answer(
    question: TechnicalQuestion, session: Annotated[Session, Depends(knowledge_session)]
) -> dict:
    return asdict(answer_technical(session, question.question))


@app.post("/v1/support/messages", response_model=CustomerMessageRead)
def customer_message(
    request: CustomerMessageInput,
    identity: Annotated[CustomerIdentity, Depends(require_customer_identity)],
) -> CustomerMessageRead:
    state = run_message(identity, request.message, order_id=request.order_id)
    if state.get("intent") == "escalation":
        _persist_escalation(identity, request, state)
    return _customer_response(state)


def _persist_escalation(
    identity: CustomerIdentity, request: CustomerMessageInput, state: dict
) -> None:
    try:
        ticket = EscalationTool().create(
            identity.customer_id,
            request.conversation_id or uuid4(),
            state.get("escalation_reason") or "human_review_required",
        )
    except ToolFailure as exc:
        raise HTTPException(status_code=503, detail="Support handoff unavailable") from exc
    state["ticket_id"] = str(ticket.ticket_id)


def _customer_response(state: dict) -> CustomerMessageRead:
    escalated = state.get("intent") == "escalation"
    return CustomerMessageRead(
        status="escalated" if escalated else "answered",
        intent=state["intent"],
        answer=state["answer"],
        evidence=state.get("evidence", []),
        tracking=state.get("tracking"),
        escalation_reason=state.get("escalation_reason") if escalated else None,
        ticket_id=state.get("ticket_id") if escalated else None,
    )


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@app.post("/v1/support/messages/stream")
def customer_message_stream(
    request: CustomerMessageInput,
    identity: Annotated[CustomerIdentity, Depends(require_customer_identity)],
) -> StreamingResponse:
    def events() -> Iterator[str]:
        state: dict = {}
        try:
            for node, update in stream_message(
                identity, request.message, order_id=request.order_id
            ):
                state.update(update)
                if node == "supervisor":
                    yield _event("routing", {"intent": update["intent"]})
                    if update["intent"] in {"technical", "fulfillment"}:
                        yield _event("tool_started", {"tool": update["intent"]})
                elif node in {"technical", "fulfillment"}:
                    if update.get("evidence"):
                        yield _event("retrieval", {"evidence": update["evidence"]})
                    yield _event(
                        "tool_finished",
                        {
                            "tool": node,
                            "status": "answered" if update.get("answer") else "escalated",
                        },
                    )
                elif node == "escalation":
                    _persist_escalation(identity, request, state)
                    yield _event(
                        "escalated",
                        {"reason": state.get("escalation_reason"), "ticket_id": state["ticket_id"]},
                    )
            yield _event("completed", _customer_response(state).model_dump(mode="json"))
        # Headers are already sent; terminate the stream without exposing backend details.
        except Exception:  # noqa: BLE001
            yield _event("error", {"message": "Support response unavailable"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/admin/tickets", response_model=TicketQueueRead)
def review_escalations(
    response: Response,
    _admin: Annotated[AdminIdentity, Depends(require_admin_identity)],
    authorization: Annotated[str, Header()],
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> TicketQueueRead:
    try:
        queue = EscalationTool().list_open(authorization, offset=offset)
    except ToolFailure as exc:
        raise HTTPException(status_code=503, detail="Escalation queue unavailable") from exc
    response.headers["Cache-Control"] = "no-store"
    return queue


def billing_checkpoint() -> Iterator[PostgresSaver]:
    try:
        dsn = database_url().set(drivername="postgresql").render_as_string(hide_password=False)
        with PostgresSaver.from_conn_string(dsn) as saver:
            saver.setup()
            yield saver
    except (PostgresError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Billing workflow unavailable") from exc


@app.post("/v1/support/billing-requests", response_model=BillingRequestRead)
def request_billing_action(
    request: BillingRequestInput,
    identity: Annotated[CustomerIdentity, Depends(require_customer_identity)],
    saver: Annotated[PostgresSaver, Depends(billing_checkpoint)],
) -> BillingRequestRead:
    graph = build_graph(TechnicalTool(), LogisticsTool(), billing=BillingTool(), checkpointer=saver)
    action = request.model_dump(mode="json")
    config = {"configurable": {"thread_id": f"{identity.customer_id}:{request.conversation_id}"}}
    snapshot = graph.get_state(config)
    if snapshot.values:
        if snapshot.values.get("billing_action") != action:
            raise HTTPException(status_code=409, detail="Conversation already used")
        interrupts = [item for task in snapshot.tasks for item in task.interrupts]
        if interrupts:
            pending = interrupts[0].value
            return BillingRequestRead(
                status="approval_required",
                request_id=pending["request_id"],
                action=pending["action"],
                answer="Your request is awaiting administrator review.",
            )
        state = snapshot.values
    else:
        state = new_state(
            identity, "refund payment" if request.action == "refund" else "cancel subscription"
        )
        state["billing_action"] = action
        result = graph.invoke(state, config=config)
        if result.get("__interrupt__"):
            pending = result["__interrupt__"][0].value
            return BillingRequestRead(
                status="approval_required",
                request_id=pending["request_id"],
                action=pending["action"],
                answer="Your request is awaiting administrator review.",
            )
        state = result
    return _billing_response(state)


def _billing_response(state: dict) -> BillingRequestRead:
    result = state.get("billing_result")
    if result and result.get("status") in {"executed", "rejected"}:
        return BillingRequestRead(
            status=result["status"],
            request_id=result["request_id"],
            action=state["billing_action"]["action"],
            answer=state["answer"],
        )
    if state.get("intent") == "escalation":
        return BillingRequestRead(
            status="escalated",
            answer=state["answer"],
            escalation_reason=state.get("escalation_reason"),
        )
    raise HTTPException(status_code=503, detail="Billing workflow unavailable")


@app.get("/v1/admin/actions", response_model=ReviewQueueRead)
def review_billing_actions(
    response: Response,
    _admin: Annotated[AdminIdentity, Depends(require_admin_identity)],
    saver: Annotated[PostgresSaver, Depends(billing_checkpoint)],
    authorization: Annotated[str, Header()],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> ReviewQueueRead:
    try:
        queue = BillingTool().list_review_actions(authorization, limit=limit, offset=offset)
    except ToolFailure as exc:
        raise HTTPException(status_code=503, detail="Review queue unavailable") from exc
    graph = build_graph(TechnicalTool(), LogisticsTool(), billing=BillingTool(), checkpointer=saver)
    ready = []
    for item in queue.items:
        try:
            conversation_id = UUID(item.conversation_id)
            config = {"configurable": {"thread_id": f"{item.customer_id}:{conversation_id}"}}
            snapshot = graph.get_state(config)
            action = BillingRequestInput.model_validate(snapshot.values["billing_action"])
        except (ValueError, KeyError):
            continue
        interrupts = [part for task in snapshot.tasks for part in task.interrupts]
        if (
            snapshot.values.get("customer_id") == str(item.customer_id)
            and action.conversation_id == conversation_id
            and action.action == item.action
            and action.target_id == item.target_id
            and action.amount_cents == item.amount_cents
            and action.reason == item.reason
            and len(interrupts) == 1
            and interrupts[0].value == {"request_id": str(item.request_id), "action": item.action}
        ):
            ready.append(item)
    response.headers["Cache-Control"] = "no-store"
    return ReviewQueueRead(items=ready, offset=queue.offset, has_more=queue.has_more)


@app.post(
    "/v1/admin/customers/{customer_id}/conversations/{conversation_id}/"
    "actions/{action}/{request_id}/decision",
    response_model=BillingRequestRead,
)
def decide_billing_action(
    customer_id: UUID,
    conversation_id: UUID,
    action: Literal["refund", "cancellation"],
    request_id: UUID,
    body: ActionDecisionInput,
    _admin: Annotated[AdminIdentity, Depends(require_admin_identity)],
    saver: Annotated[PostgresSaver, Depends(billing_checkpoint)],
    authorization: Annotated[str, Header()],
) -> BillingRequestRead:
    tool = BillingTool()
    graph = build_graph(TechnicalTool(), LogisticsTool(), billing=tool, checkpointer=saver)
    config = {"configurable": {"thread_id": f"{customer_id}:{conversation_id}"}}
    snapshot = graph.get_state(config)
    values = snapshot.values
    if not values or values.get("customer_id") != str(customer_id):
        raise HTTPException(status_code=404, detail="Request not found")
    try:
        original = BillingRequestInput.model_validate(values["billing_action"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Request not found") from exc
    if original.conversation_id != conversation_id or original.action != action:
        raise HTTPException(status_code=404, detail="Request not found")
    completed = values.get("billing_result")
    if completed:
        if completed.get("request_id") != str(request_id):
            raise HTTPException(status_code=404, detail="Request not found")
        expected = "executed" if body.decision == "approve" else "rejected"
        if completed.get("status") != expected:
            raise HTTPException(status_code=409, detail="Request already decided")
        return _billing_response(values)
    interrupts = [item for task in snapshot.tasks for item in task.interrupts]
    if len(interrupts) != 1 or interrupts[0].value != {
        "request_id": str(request_id),
        "action": action,
    }:
        raise HTTPException(status_code=404, detail="Request not found")
    try:
        decided = tool.decide(customer_id, original, request_id, body.decision, authorization)
        expected = "approved" if body.decision == "approve" else "rejected"
        if decided.status not in (
            {expected, "executed"} if body.decision == "approve" else {expected}
        ):
            raise HTTPException(status_code=409, detail="Request already decided")
        result = graph.invoke(
            Command(resume={"decision": body.decision, "request_id": str(request_id)}),
            config=config,
        )
    except ToolFailure as exc:
        status_code = 409 if exc.code == "conflict" else 503
        raise HTTPException(status_code=status_code, detail="Billing workflow unavailable") from exc
    response = _billing_response(result)
    if response.status not in {"executed", "rejected"}:
        raise HTTPException(status_code=503, detail="Billing workflow unavailable")
    return response
