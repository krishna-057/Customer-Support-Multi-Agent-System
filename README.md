# Customer Support Multi-Agent System

A customer support platform with scoped technical, billing, fulfillment, and escalation workflows. The implementation follows [the technical plan](docs/technical-plan.pdf) and the milestones in [PLAN.md](PLAN.md).

GitHub: https://github.com/krishna-057/Customer-Support-Multi-Agent-System

The central rule is that model output may interpret a request, but backend services own identity, authorization, policy, state changes, and audit events. A refund or cancellation must pause for human approval and resume the same thread before the backend executes it.

## Architecture

- A FastAPI gateway streams conversation progress through SSE.
- A stateful graph routes requests to narrow technical, billing, fulfillment, and escalation workflows.
- The CRM service owns account data, billing policy, authorization, idempotency, and audit records.
- The logistics service owns order and tracking data.
- PostgreSQL stores business state and support-document embeddings; technical answers require retrieved evidence.
- Sensitive billing actions pause for an administrator's decision before the backend executes them.

## Development

The application is in planning. The first implementation milestone establishes service contracts and a reproducible local environment. Later milestones add the CRM, logistics, retrieval, orchestration, interface, and evaluation paths. See [PLAN.md](PLAN.md) for the sequence and [DECISIONS.md](DECISIONS.md) for design tradeoffs.

Run instructions will be added as the services are implemented.
