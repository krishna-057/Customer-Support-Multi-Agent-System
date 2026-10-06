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

## 2026-10-02 - Scope deterministic logistics reads by customer

- Context: Fulfillment orchestration needs a stable order/tracking contract and repeatable service-failure cases before any agent tool can use it. The customer gateway and live carrier adapter do not exist yet.
- Decision: Add read-only internal order-list and tracking endpoints guarded by a dedicated logistics service credential. Share only the synthetic fixture ID algorithm with CRM; filter lists by customer and return the same 404 for unknown and other-customer orders. Return typed order summaries and ordered tracking events. Reserve three order IDs for deterministic 429, 503, and emulated upstream-timeout 504 responses; credential failures supply the 403 case.
- Alternatives considered: A public order-ID route would disclose another customer's tracking data. Caller-selectable failure headers would expose test behavior in normal requests. Real sleeps would make the timeout fixture slow and would not reliably test the HTTP contract.
- Consequences and validation: Contract tests cover scoped lists, event ordering, credential denial, cross-customer isolation, invalid identifiers, and all failure statuses. The gateway must still bind an authenticated customer before it may use this service token. The 504 fixture validates response handling only; future client integration must test real network timeouts. Docker Compose boot remains open while Docker is unavailable.

## 2026-10-03 - Gate technical answers on versioned evidence

- Context: The agent needs an evidence source before it can answer technical questions. There is no model credential or locally available pgvector extension, and the graph and customer auth are later gates.
- Decision: Bundle 20 synthetic help articles with stable IDs, sections, and a revision. Seed them idempotently into PostgreSQL with a 256-dimensional hash embedding and pgvector HNSW cosine index. Keep retrieval behind a distinct internal service token. Return only an article's stored answer with its ID citation and metadata when cosine similarity and question-anchor overlap clear fixed thresholds; otherwise return an escalation result without an answer. Agent readiness requires seeded knowledge.
- Alternatives considered: An external embedding or answer model would add a credential dependency and could fabricate unsupported claims. A lexical-only database search would omit the planned vector index. A public answer endpoint would expose a capability before customer identity is bound.
- Consequences and validation: Deterministic retrieval is reproducible but handles paraphrases poorly; the threshold is not calibrated confidence. Unit and API tests cover all 20 known questions, low-confidence and adversarial queries, and token denial. CI on the pgvector PostgreSQL image passed migration/rollback/re-migration, idempotent article seeding, vector index and query tests, Alembic drift check, and all 37 tests. Local Compose boot remains unverified while Docker Desktop's Linux engine is unavailable. The future graph should call the same evidence decision and preserve escalation rather than substituting model output.

## 2026-10-03 - Bind identity before workflow tools

- Context: The technical and logistics services have narrow tokens but the agent gateway has no customer authentication. Passing a caller-supplied customer ID into a graph would let a service credential read another customer's orders. The LangGraph package could not be installed in the current restricted environment, so graph assembly cannot be validated yet.
- Decision: Add a short-lived HMAC-signed customer-session verifier with no public minting route. Construct workflow state from verified claims, not request data. Keep technical and logistics HTTP adapters separate so each holds only its own credential, validates typed responses and ownership, and maps upstream failures to sanitized codes. Use deterministic conservative supervisor/node functions as independently testable inputs to the later graph; route billing, mixed, unknown, and explicit-human requests to escalation while sensitive execution remains absent.
- Alternatives considered: Trusting a customer ID in the request, sharing service tokens with the browser, or calling backend tables directly from the agent would weaken the service boundary. Committing untested LangGraph integration without the package and lockfile would break the CI installation gate.
- Consequences and validation: Session signature, expiry, malformed-token, scope, ownership, routing, evidence, and failure tests pass locally. No authenticated public route, live LangGraph graph, checkpointer, billing node, or persisted escalation ticket exists yet. A future login flow must issue sessions, and the graph gate remains open until LangGraph can be installed, locked, and exercised end to end.

## 2026-10-03 - Compile a stateless, scoped LangGraph before approval persistence

- Context: Verified identity and narrow node functions are available, but routing them directly from an endpoint would bypass the planned graph boundary. The workflow cannot safely retain conversations or execute billing changes until persisted approval and resume exist.
- Decision: Compile a StateGraph with a conservative supervisor and technical, fulfillment, and escalation branches. Tool failures and weak evidence route to escalation. Expose one signed-session-only message route that constructs state from verified claims and returns only a typed customer response; do not expose handoff internals, service credentials, or caller-supplied customer IDs. Keep each invocation stateless, with no checkpointer or billing execution.
- Alternatives considered: A public customer-ID route would allow spoofing; an in-memory checkpointer would imply durability it does not have; connecting the billing request API before administrator approval and resume would create an incomplete sensitive workflow.
- Consequences and validation: Graph and gateway tests cover routing, evidence, authenticated identity, customer scoping, billing escalation, and sanitized response shape. A trusted login service must issue sessions later. Conversation persistence, rate limiting, durable handoff, administrator approval, and exactly-once execution remain later gates.

## 2026-10-04 - Persist administrator decisions before financial execution

- Context: CRM action requests are durable and idempotent, but a billing workflow cannot safely execute a refund or cancellation until an administrator decision is authenticated, immutable under retries, and audited. The graph still has no durable checkpoint or billing node.
- Decision: Use a separate short-lived signed administrator session issuer and secret, with no public minting route. Accept approve/reject only for pending refund or cancellation requests. Lock the request row in PostgreSQL and commit its status transition and an audit event with the verified administrator ID in one transaction. Identical retries return the existing state; conflicting decisions fail with 409. Keep payment and subscription mutations closed.
- Alternatives considered: Reuse the billing-agent request credential for decisions, which would let an agent authorize itself; accept an administrator ID from request JSON, which is spoofable; or execute on approval before same-thread resume and duplicate-execution protection exist. All violate the intended separation of duties.
- Consequences and validation: The CRM now records an administrator decision independently of the graph, but this is only a partial approval gate. A trusted administrator login must issue sessions, and the billing graph still needs a persisted interrupt and same-thread resume before final execution is opened. Local tests cover credential/issuer separation, invalid decisions, replay/conflict behavior, audit attribution, and unchanged financial state. PostgreSQL integration and CI provide the row-locking path; local Compose boot remains unverified.

## 2026-10-04 - Make mock execution an approved-only replay-safe CRM operation

- Context: LangGraph restarts an interrupted node on resume, so a financial mutation called after approval must be safe under retries, including concurrent requests. Administrator approval exists, but the graph still lacks a durable interrupt and same-thread resume.
- Decision: Add a fourth CRM-only service credential dedicated to execution; do not give it to the agent service yet. Require the verified customer ID and original conversation ID, an approved request, and a locked recheck of account and target eligibility. In one database transaction, update the local mock payment/refund or subscription state, mark the request executed, and add an execution audit event. Return the existing result for identical retries. Keep blocked requests approved for operator resolution and record at most one blocked-attempt audit event per request.
- Alternatives considered: Reuse the request-creation or administrator credential, execute at approval time, or rely only on graph state to prevent duplicates. Each would weaken separation of duties or leave a crash/retry window that could duplicate a write. A real payment-provider call is outside this local mock gate and would need provider-level idempotency and reconciliation.
- Consequences and validation: This is an internal local-ledger operation, not a real refund; no customer or agent route can invoke it yet. The billing graph must still checkpoint, interrupt, authenticate the administrator, resume the same thread, and only then receive the execute credential. Unit/API tests cover unauthorized, pending, rejected, wrong-customer, wrong-conversation, partial/full refund, cancellation, changed eligibility, and replay cases. GitHub Actions run `37177821279` passed the PostgreSQL concurrent-retry test with one financial update and one execution audit event. Full local Compose boot remains unverified.

## 2026-10-05 - Persist a pending billing interrupt without opening execution

- Context: CRM request creation, administrator decisions, and replay-safe mock execution exist independently, but the customer workflow still escalates all billing requests. A billing action must be tied to verified customer identity and a durable conversation before any approval can safely resume it.
- Decision: Add a separate signed-session-only structured billing request route and a fifth graph node. The agent holds only the CRM request token, not the execute token. It derives the thread ID from customer and conversation UUIDs and a stable CRM idempotency key from the conversation UUID. The node creates a pending CRM request and interrupts on a PostgreSQL checkpoint. Repeated requests with the same thread and payload return the saved interrupt; changed payloads conflict. An arbitrary low-level resume escalates rather than claiming approval or executing. Unstructured billing chat still escalates.
- Alternatives considered: Accepting free-form model output as the billing action would make amounts and targets ambiguous. An in-memory checkpoint would lose the approval state on restart. Granting the execute token before an authenticated admin decision and same-thread resume would bypass the safety gate.
- Consequences and validation: Checkpoint tables are initialized by the PostgreSQL saver on the billing path. Checkpoints retain the structured reason, so production use requires retention/privacy controls. The graph has no public admin resume or execution route yet; Day 7 remains incomplete. Unit tests cover identity binding, typed adapter ownership, interruption, same-payload replay, changed-payload conflict, and a resume that fails closed. A PostgreSQL reconnection test is enabled in CI; local Docker/PostgreSQL execution remains unavailable.

## 2026-10-05 - Bind admin decisions to the paused graph thread

- Context: The CRM can decide and execute requests independently, while the graph has a durable pending interrupt. The missing link must not let a caller approve an unrelated request, resume another customer's thread, or treat a failed execution as completed.
- Decision: Require a signed administrator session at the agent decision route. Before contacting CRM, match customer, conversation, action, and request IDs against the paused checkpoint. Forward the same administrator session to CRM for the audited decision, then resume that thread with only the verified decision and request ID. The billing node rereads the idempotent CRM request state and executes only when CRM reports approved or already executed; rejection never calls execution. The execute credential is server-side in agent-api and CRM, and CRM still enforces approved status, customer/conversation binding, policy, row locks, and idempotency. Identical approval after execution returns the existing CRM result; contradictory decisions fail. Tool errors leave the interrupted graph retryable rather than reporting success.
- Alternatives considered: Trusting a decision in the resume payload, allowing a public graph-resume endpoint, or executing immediately in the administrator decision endpoint would bypass the checkpoint and separation of duties. Keeping execution outside the graph would not prove same-thread continuation.
- Consequences and validation: An admin route now completes the local mock action after the same thread resumes. The server-side execute credential increases agent-api privilege but cannot bypass CRM's approved-only checks. A frozen exception class prevented LangGraph from attaching a traceback on an execution error; it is mutable now, and the retry path is tested. Public session issuance, an admin queue/UI, checkpoint retention, rate limiting, and real provider integration remain out of scope. Tests cover approval, rejection, mismatched IDs, conflicting replay, execution failure/retry, and database-backed resume across requests; full local Compose boot remains unverified.

## 2026-10-06 - Stream customer-visible graph progress

- Context: The Day 8 chat UI needs progress without making a second request or exposing internal graph state. The existing signed-session message endpoint returns only after the graph finishes.
- Decision: Add a parallel authenticated POST SSE endpoint that streams actual LangGraph node updates. Publish routing and tool start/finish markers, technical evidence when present, an escalation reason, and a final response with the same typed customer shape as the REST endpoint. Send a generic error event if execution fails after response headers are sent. Keep billing approval on its separate authenticated REST routes.
- Alternatives considered: Polling duplicates work and complicates correlation; a WebSocket is unnecessary for one-way progress; forwarding raw graph updates would disclose customer identity and handoff details. Token-level message chunks would imply generation behavior this deterministic workflow does not have.
- Consequences and validation: Streaming is node-level rather than token-level. The frontend can consume it with fetch and can distinguish a completed response from a stream error. Tests cover authorization, event order, evidence, escalation, output redaction, and sanitized failures. This does not provide session issuance, a persisted escalation ticket, or the Day 8 browser UI; those gates remain open.
