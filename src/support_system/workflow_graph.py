"""Compile the scoped support workflow without persistence or billing execution."""

from uuid import UUID

from langgraph.graph import END, START, StateGraph

from support_system.customer_identity import CustomerIdentity
from support_system.scoped_tools import LogisticsTool, TechnicalTool
from support_system.workflow_nodes import (
    LogisticsPort,
    SupportState,
    TechnicalPort,
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


def build_graph(technical: TechnicalPort, logistics: LogisticsPort):
    builder = StateGraph(SupportState)
    builder.add_node("supervisor", supervisor)
    builder.add_node("technical", lambda state: technical_node(state, technical))
    builder.add_node("fulfillment", lambda state: fulfillment_node(state, logistics))
    builder.add_node("escalation", escalation_node)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"technical": "technical", "fulfillment": "fulfillment", "escalation": "escalation"},
    )
    for node in ("technical", "fulfillment"):
        builder.add_conditional_edges(
            node, _after_tool, {"escalation": "escalation", "complete": END}
        )
    builder.add_edge("escalation", END)
    return builder.compile()


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
