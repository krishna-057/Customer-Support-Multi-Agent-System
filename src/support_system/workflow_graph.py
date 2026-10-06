"""Compile scoped support workflows; billing pauses before administrator review."""

from collections.abc import Iterator
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from support_system.customer_identity import CustomerIdentity
from support_system.scoped_tools import LogisticsTool, TechnicalTool
from support_system.workflow_nodes import (
    BillingPort,
    LogisticsPort,
    SupportState,
    TechnicalPort,
    billing_node,
    escalation_node,
    fulfillment_node,
    new_state,
    route_after_supervisor,
    supervisor,
    technical_node,
)


def _after_tool(state: SupportState) -> str:
    if state.get("intent") == "escalation" or not state.get("answer"):
        return "escalation"
    return "complete"


def build_graph(
    technical: TechnicalPort,
    logistics: LogisticsPort,
    *,
    billing: BillingPort | None = None,
    checkpointer=None,
):
    builder = StateGraph(SupportState)
    builder.add_node("supervisor", supervisor)
    builder.add_node("technical", lambda state: technical_node(state, technical))
    builder.add_node("fulfillment", lambda state: fulfillment_node(state, logistics))
    if billing is not None:
        builder.add_node("billing", lambda state: billing_node(state, billing))
    builder.add_node("escalation", escalation_node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "technical": "technical",
            "fulfillment": "fulfillment",
            "billing": "billing" if billing else "escalation",
            "escalation": "escalation",
        },
    )
    for node in ("technical", "fulfillment"):
        builder.add_conditional_edges(
            node, _after_tool, {"escalation": "escalation", "complete": END}
        )
    builder.add_edge("escalation", END)
    if billing is not None:
        builder.add_conditional_edges(
            "billing", _after_tool, {"escalation": "escalation", "complete": END}
        )
    return builder.compile(checkpointer=checkpointer)


def run_message(
    identity: CustomerIdentity,
    message: str,
    *,
    order_id: UUID | None = None,
    technical: TechnicalPort | None = None,
    logistics: LogisticsPort | None = None,
) -> SupportState:
    graph = build_graph(technical or TechnicalTool(), logistics or LogisticsTool())
    return graph.invoke(new_state(identity, message, order_id=order_id))


def stream_message(
    identity: CustomerIdentity,
    message: str,
    *,
    order_id: UUID | None = None,
    technical: TechnicalPort | None = None,
    logistics: LogisticsPort | None = None,
) -> Iterator[tuple[str, SupportState]]:
    graph = build_graph(technical or TechnicalTool(), logistics or LogisticsTool())
    for update in graph.stream(new_state(identity, message, order_id=order_id)):
        yield from update.items()
