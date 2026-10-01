# Decisions

Append a dated entry whenever a meaningful design, scope, safety, or deployment decision is made. Use this form:

## YYYY-MM-DD - Short decision title

- Context:
- Decision:
- Alternatives considered:
- Consequences and validation:

## 2026-09-28 - V1 implementation sequence

- Context: The source plan has eight phases and a broad production-style V1.
- Decision: Establish service contracts and backend write safety first, then retrieval and orchestration, then the UI and evaluation. Treat cloud deployment and a real CRM provider as optional after the local V1 gates pass.
- Alternatives considered: Building all agents and UI first would make unsafe or untestable backend behavior harder to correct. Attempting every optional integration would dilute the core workflows.
- Consequences and validation: The gates in `PLAN.md` determine progress. Unmet scope remains explicit at release time.

## 2026-09-29 - Isolate business routes behind backend gates

- Context: The first gate needs bootable service boundaries, while CRM writes must not exist before authorization, idempotency, and audit rules are implemented.
- Decision: Create separate agent, CRM, and logistics FastAPI applications with only health endpoints. Share typed health contracts. CRM readiness queries PostgreSQL and returns a generic 503 on connection failure; liveness does not depend on the database.
- Alternatives considered: Stub refund and chat routes now, or allow CRM readiness to report healthy without a database. Both could imply unavailable or unsafe behavior is operational.
- Consequences and validation: Business routes remain 404 until later gates. Smoke tests cover each service's liveness, CRM fail-closed readiness, and non-disclosure of connection details. Full Compose boot still requires a running Docker engine.

## 2026-09-29 - Keep local database files in the checkout

- Context: Local PostgreSQL must be reproducible, and project data should stay with the active development checkout.
- Decision: Use a PostgreSQL 17 image with pgvector 0.8.6, initialize the extension once, bind database files to ignored `./data/postgres`, and bind exposed ports to loopback. Keep the password in ignored `.env`; pass it to CRM as a separate environment field rather than embedding it in a URL.
- Alternatives considered: A Docker named volume could store data outside the project, and a credential-bearing URL would require escaping passwords with reserved characters.
- Consequences and validation: Compose syntax validates with a supplied password. Container startup and vector extension creation remain to be verified when Docker is available.

## 2026-10-01 - Gate CRM reads with distinct service credentials

- Context: Account and billing reads are needed before the agent gateway exists. A public customer-ID route would disclose cross-customer data, while financial execution still requires later approval and audit gates.
- Decision: Add versioned SQLAlchemy/Alembic tables for customers, subscriptions, invoices, payments, action requests, tickets, and audit events. Expose account and billing reads only under `/internal`, with distinct service credentials supplied in the environment. The billing credential may also read account summaries; the account credential cannot read billing records. Both must be configured and different. Every query filters by the requested customer ID. Keep all sensitive write routes closed.
- Alternatives considered: A caller-supplied customer header or ID alone is spoofable. A shared read token would not distinguish financial access. Building user sessions inside the CRM before the gateway exists would couple two service responsibilities.
- Consequences and validation: The agent gateway must authenticate the customer and bind its ID before using these contracts; the service credentials must never reach the browser. Contracts omit payment provider references. Tests cover missing/wrong/equal tokens, financial scope, other-customer isolation, 404/422, and real PostgreSQL reads. Approval and execution authorization remain the next gate.

## 2026-10-01 - Seed repeatable synthetic CRM cases

- Context: Billing flows need stable normal and edge-case records for tests and evaluation without using real customer information.
- Decision: Seed 25 baseline and 12 named edge-case customers with deterministic IDs, Faker-generated display names, `.example.test` addresses, integer minor-unit amounts, and explicit payment/refund balances. Run the seed after migrations; re-running skips existing fixture customers. The first migration also reserves request, ticket, and audit tables with uniqueness and money-bound constraints for later write workflows.
- Alternatives considered: Random IDs on each run would break repeatable tests; manually entered real-looking contact data creates needless privacy risk. A seed that deletes all existing rows would destroy local work.
- Consequences and validation: A clean PostgreSQL migration/rollback/re-migration created the tables; seed inserted 37 customers then 0 on repeat, and Alembic reported no schema drift. The local pgvector Compose boot check remains open because Docker is unavailable.

## 2026-10-01 - Persist action requests without executing them

- Context: The CRM must accept billing-agent refund and cancellation intents, but the human approval/checkpoint flow is a later gate. A retry or a second request for the same target must not produce competing pending actions.
- Decision: Add a third, distinct service credential for request creation. Validate customer ownership and simple deterministic eligibility in the CRM: active account, paid invoice and charged payment with enough unrefunded balance for refunds; active account and uncancelled active subscription for cancellations. Require a bounded idempotency key and conversation ID. Persist each pending request with its creation audit event atomically; audit policy denials without changing financial state. Audit data captures target ID, requested amount when relevant, and outcome but omits the free-text reason. A repeated key and identical payload returns the existing request. Distinct keys cannot create simultaneous pending/approved requests for one payment or subscription because PostgreSQL partial unique indexes enforce that invariant.
- Alternatives considered: Prompt-only policy and in-memory duplicate checks would fail under retries or concurrent workers. Executing the financial change during request creation would bypass the required administrator decision. A shared billing read/write credential would overgrant the read tool.
- Consequences and validation: No endpoint approves, rejects, executes, refunds, or cancels yet. Tests cover role denial, invalid input, cross-customer IDs, policy denials, duplicate keys, competing requests, atomic audit creation, and unchanged financial state. A later gate must bind an authenticated customer and administrator to the conversation, implement persisted approval/rejection and same-thread resume, and protect final execution with its own idempotency check. Compose boot is still unverified without Docker.
