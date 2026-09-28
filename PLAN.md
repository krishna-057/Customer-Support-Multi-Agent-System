# Ten-day implementation plan

Source: `docs/technical-plan.pdf`. This sequence targets its V1 scope. Dates are estimates and the gates determine readiness.

| Day | Date (IST) | Primary deliverable | Gate |
| --- | --- | --- | --- |
| 1 | 2026-09-29 | Monorepo skeleton, Compose PostgreSQL/pgvector, FastAPI service shells, shared contracts, CI baseline | Services boot and smoke tests pass |
| 2 | 2026-09-30 | CRM schema, migrations, seed data, customer/account read APIs | Migration and read-contract tests pass |
| 3 | 2026-10-01 | CRM scoped authorization, policy checks, refund/cancellation requests, idempotency, audit trail | Unauthorized and duplicate-write tests pass |
| 4 | 2026-10-02 | Logistics order/tracking API and deterministic 403/404/429/503/timeout fixtures; stabilize service contracts | CRM and logistics contract tests pass |
| 5 | 2026-10-03 | Support article corpus, pgvector indexing, retrieval evidence and confidence gate | Known answers cite evidence; weak matches escalate |
| 6 | 2026-10-04 | LangGraph state and supervisor with technical, fulfillment, and escalation nodes | Routing and scoped-tool tests pass |
| 7 | 2026-10-05 | Billing node, persisted interrupt, admin approval/rejection and same-thread resume | Resume and duplicate-execution tests pass |
| 8 | 2026-10-06 | Next.js customer chat, SSE progress, admin approval and escalation views | End-to-end UI workflows pass locally |
| 9 | 2026-10-07 | Traces, redaction, curated evaluation cases, normal/ambiguous/adversarial/failure runs | Evaluation metrics and failures are recorded |
| 10 | 2026-10-08 | Auth/rate-limit hardening, CI gate, Docker reproducibility, final demo and deployment readiness | Full integration/evaluation suite passes; publish only if credentials and hosting are ready |

## Scope control

Use one customer chat, one admin view, one CRM service, one logistics service, and five graph nodes: supervisor, technical, billing, fulfillment, escalation. Build the local mock CRM first. A real CRM adapter and cloud deployment are optional after the local V1 passes its gates.

The technical plan calls for 100-200 curated conversations. Start with representative cases and expand the corpus after the workflows are stable. Track the achieved count and measured outcomes.

## Non-negotiable gates

1. Stable, tested CRM and logistics contracts before agent integration.
2. Backend authorization, idempotency, and audit logging before sensitive execution.
3. Retrieved evidence and a confidence decision before any technical answer.
4. End-to-end tests for normal, ambiguous, adversarial, and service-failure cases before claiming portfolio readiness.
5. Secrets stay in ignored local environment files or an approved secret manager. Customer/admin identity is enforced server-side. Traces minimize PII.

Blocked gates should remain open until their failing checks are resolved.
