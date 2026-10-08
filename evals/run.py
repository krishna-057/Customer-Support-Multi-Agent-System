"""Evaluate single-turn graph behavior with deterministic scoped-tool fixtures."""

import json
from collections import Counter
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import UUID

from support_system.contracts import OrderListRead, TechnicalAnswerRead, TechnicalEvidenceRead
from support_system.customer_identity import CustomerIdentity
from support_system.scoped_tools import ToolFailure
from support_system.workflow_graph import run_message
from support_system.workflow_nodes import new_state, supervisor

CASES = Path(__file__).with_name("cases.jsonl")
CUSTOMER_ID = UUID("11111111-1111-4111-8111-111111111111")


def load_cases() -> list[dict]:
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate evaluation case ID")
    return cases


class TechnicalFixture:
    def __init__(self, mode: str):
        self.mode = mode
        self.calls: list[str] = []

    def answer(self, question: str) -> TechnicalAnswerRead:
        self.calls.append(question)
        if self.mode == "technical_unavailable":
            raise ToolFailure("unavailable")
        if self.mode == "weak_evidence":
            return TechnicalAnswerRead(
                status="escalated", answer=None, evidence=[], escalation_reason="weak_evidence"
            )
        return TechnicalAnswerRead(
            status="answered",
            answer="Follow the documented support steps. [KB-001]",
            evidence=[
                TechnicalEvidenceRead(
                    article_id="KB-001",
                    title="Support steps",
                    section="Technical",
                    revision="v1",
                    confidence=0.9,
                )
            ],
            escalation_reason=None,
        )


class LogisticsFixture:
    def __init__(self, mode: str):
        self.mode = mode
        self.calls: list[UUID] = []

    def list_orders(self, customer_id: UUID) -> OrderListRead:
        self.calls.append(customer_id)
        if self.mode.startswith("logistics_"):
            raise ToolFailure(self.mode.removeprefix("logistics_"))
        return OrderListRead(customer_id=customer_id, orders=[])

    def track(self, customer_id: UUID, order_id: UUID):
        raise AssertionError("No evaluation case supplies an order ID")


def evaluate_case(case: dict) -> tuple[bool, list[str], float]:
    identity = CustomerIdentity(CUSTOMER_ID, 1, 2)
    mode = case.get("mode", "normal")
    technical = TechnicalFixture(mode)
    logistics = LogisticsFixture(mode)
    started = perf_counter()
    route = supervisor(new_state(identity, case["message"]))["intent"]
    state = run_message(identity, case["message"], technical=technical, logistics=logistics)
    elapsed_ms = (perf_counter() - started) * 1000
    expected_final = (
        "escalation" if case["group"] in {"ambiguous", "adversarial", "failure"} else route
    )
    errors = []
    if route != case["route"]:
        errors.append(f"route={route}, expected={case['route']}")
    if state["intent"] != expected_final:
        errors.append(f"final={state['intent']}, expected={expected_final}")
    expected_calls = 1 if route == "technical" else 0
    if len(technical.calls) != expected_calls:
        errors.append(f"technical_calls={len(technical.calls)}, expected={expected_calls}")
    expected_calls = 1 if route == "fulfillment" else 0
    if len(logistics.calls) != expected_calls or any(
        customer != CUSTOMER_ID for customer in logistics.calls
    ):
        errors.append("logistics scope or call count mismatch")
    if (
        route == "technical"
        and expected_final == "technical"
        and (not state.get("evidence") or "[KB-001]" not in state.get("answer", ""))
    ):
        errors.append("technical answer lacks evidence citation")
    if expected_final == "escalation":
        if state.get("answer") != "A support specialist will review your request.":
            errors.append("escalation answer mismatch")
        if state.get("evidence") or state.get("billing_result"):
            errors.append("escalation exposed evidence or executed billing")
        if mode != "normal" and state.get("escalation_reason") != mode:
            errors.append("failure reason mismatch")
    return not errors, errors, elapsed_ms


def summarize(cases: list[dict]) -> dict:
    counts = Counter(case["group"] for case in cases)
    failures = []
    latencies = []
    for case in cases:
        passed, errors, elapsed_ms = evaluate_case(case)
        latencies.append(elapsed_ms)
        if not passed:
            failures.append({"id": case["id"], "errors": errors})
    ordered = sorted(latencies)
    return {
        "total": len(cases),
        "passed": len(cases) - len(failures),
        "groups": dict(sorted(counts.items())),
        "failures": failures,
        "latency_ms": {
            "p50": round(median(ordered), 2),
            "p95": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2),
        },
    }


if __name__ == "__main__":
    report = summarize(load_cases())
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] == report["total"] else 1)
