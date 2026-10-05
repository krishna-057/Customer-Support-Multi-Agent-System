"""Agent API shell and guarded technical evidence endpoint."""

import hmac
import os
from collections.abc import Iterator
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
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
    ServiceName,
    TechnicalAnswerRead,
    TechnicalQuestion,
)
from support_system.crm_models import SupportArticle
from support_system.customer_identity import CustomerIdentity, require_customer_identity
from support_system.db import database_url
from support_system.scoped_tools import BillingTool, LogisticsTool, TechnicalTool, ToolFailure
from support_system.support_knowledge import answer_technical
from support_system.workflow_graph import build_graph, run_message
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
    escalated = state.get("intent") == "escalation"
    return CustomerMessageRead(
        status="escalated" if escalated else "answered",
        intent=state["intent"],
        answer=state["answer"],
        evidence=state.get("evidence", []),
        tracking=state.get("tracking"),
        escalation_reason=state.get("escalation_reason") if escalated else None,
    )


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
