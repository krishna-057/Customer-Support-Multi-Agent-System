# Customer Support Multi-Agent System

A customer support platform with scoped technical, billing, fulfillment, and escalation workflows. The implementation follows [the technical plan](docs/technical-plan.pdf) and the milestones in [PLAN.md](PLAN.md).

GitHub: https://github.com/krishna-057/Customer-Support-Multi-Agent-System

The central rule is that model output may interpret a request, but backend services own identity, authorization, policy, state changes, and audit events. A refund or cancellation must pause for human approval and resume the same thread before the backend executes it.

## Target architecture

- A FastAPI gateway streams conversation progress through SSE.
- A stateful graph routes requests to narrow technical, billing, fulfillment, and escalation workflows.
- The CRM service owns account data, billing policy, authorization, idempotency, and audit records.
- The logistics service owns order and tracking data.
- PostgreSQL stores business state and support-document embeddings; technical answers require retrieved evidence.
- Sensitive billing actions pause for an administrator's decision before the backend executes them.

## Current implementation

The backend provides three FastAPI services, PostgreSQL 17/pgvector Compose,
versioned CRM migrations, deterministic synthetic fixtures, guarded account and
billing reads, approval-bound refund/cancellation request creation, and scoped
logistics order/tracking reads. A 20-article synthetic knowledge base has a
pgvector cosine index and a guarded, evidence-bearing technical answer endpoint.
Workflow foundations include signed customer-session verification, narrow
technical/logistics HTTP adapters, and deterministic supervisor, technical,
fulfillment, billing, and escalation node functions. A compiled LangGraph now routes
those nodes behind a signed-session-only message endpoint.
The CRM can now persist an administrator's approve/reject decision on a pending
request without changing payment or subscription state. Financial execution
is now available only through a separate CRM-internal mock-execution credential.
The agent creates customer-scoped pending actions and persists PostgreSQL
interrupts. A signed administrator can approve or reject a paused request and
resume that exact thread. Approved actions execute only through CRM's guarded,
idempotent local mock endpoint; no payment provider is contacted.
See [PLAN.md](PLAN.md) for sequencing and [DECISIONS.md](DECISIONS.md) for tradeoffs.

The `web/` Next.js app is the customer chat interface. It consumes the SSE
gateway through a fixed same-origin route. No customer or administrator login
issuer exists yet: for local use, supply a valid short-lived customer session
from a trusted integration. The browser keeps it only in memory, and the proxy
forwards it only to the configured agent API. No CRM or execution credential is
exposed to the frontend. The admin approval queue is available at `/admin`;
the escalation dashboard is not implemented yet.

## Local development

Use Python 3.12, uv, and Docker Compose. Keep uv's cache and local data in this
checkout. In PowerShell:

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.cache/uv'
uv sync --extra dev --frozen
uv run ruff check .
uv run pytest -q
```

For the customer chat, use Node.js 24 and pnpm 11. From `web/`, run
`pnpm install --frozen-lockfile` and `pnpm dev`; open
`http://127.0.0.1:3000`. The Next.js server forwards requests to
`AGENT_API_URL` (default `http://127.0.0.1:8000`). The agent API and its
dependencies must be running for support answers. The customer session is not
stored in browser storage or committed to the repository.
The admin review page at `/admin` similarly needs a short-lived signed
administrator session from a trusted issuer. It shows pending or approved CRM
actions only when the agent finds a matching paused graph checkpoint. Approve
and reject send the decision to the existing authenticated resume endpoint;
approval executes only the guarded local mock action. Sessions and review
details are not cached by the browser proxies. There is no public login flow.

Copy `.env.example` to ignored `.env` and replace the sample password, six
service tokens, and customer/admin session secrets with unique, distinct local values.
Then run `docker compose up --build`. The CRM container applies migrations and inserts 37 synthetic
customers and 20 versioned support articles on startup. PostgreSQL data is
bind-mounted at `./data/postgres`; the pgvector extension is created when the
database is first initialized. Only loopback ports are exposed: agent API 8000,
CRM API 8001, logistics API 8002, and PostgreSQL 54329 by default.

Each service exposes `/health/live` and `/health/ready`. CRM readiness queries
PostgreSQL and returns 503 without a working database. Agent readiness requires
a seeded knowledge base; logistics readiness reports process readiness.
The image installs pinned runtime dependencies
from `requirements.txt`, exported from `uv.lock`.

For a separately running PostgreSQL instance, set `DB_HOST`, `DB_PORT`,
`DB_NAME`, `DB_USER`, and `DB_PASSWORD`, then run `uv run alembic upgrade head`
and `uv run python -m support_system.seed_crm`. Re-running the seed is safe and
reports zero new customers. `uv run alembic check` detects schema drift.

Run `uv run python -m support_system.seed_articles` after migration to insert
or revise the bundled support articles. Repeating the seed leaves unchanged
articles intact and does not remove other records.

### Technical evidence

`POST /internal/technical/answer` accepts `{"question":"..."}` and requires
`Authorization: Bearer <TECHNICAL_RETRIEVAL_TOKEN>`. Only the server-side graph
may use that credential. Known questions return the stored support answer with
an article-ID citation plus title, section, revision, and confidence metadata.
Weak matches return `status: escalated`, no answer, and an escalation reason.
The agent API fails closed with 503 if the knowledge database is unavailable.

The current 256-dimensional hash embedding is deterministic and lexical. It
does not provide model-level semantic paraphrase understanding. The confidence
threshold is a conservative local rule, not a calibrated probability; broader
retrieval evaluation is a later gate. Articles describe a synthetic demo help
center and should not be presented as live product policy. The database uses a
pgvector HNSW cosine index, while retrieval ranks candidates by cosine distance.

### Workflow boundary

`customer_identity.issue_session` is an internal signing primitive for a future
authenticated login flow; no public session-issuing route exists. A verified
short-lived token yields the customer ID used to build workflow state. The
technical and logistics adapters hold only their respective server-side service
tokens, set a three-second HTTP timeout, validate response contracts and
customer/order ownership, and return sanitized failure codes. The deterministic
supervisor escalates ambiguous, unstructured billing, explicit-human, and unknown requests.
The technical node requires evidence; the fulfillment node passes only the
verified customer ID to the logistics service; the escalation node prepares a
handoff. The graph wires these functions behind an authenticated route; only
structured billing requests have persistent checkpoints. Session issuance remains future work.

### Customer workflow

`POST /v1/support/messages` accepts a message (and optional order ID) with a
short-lived signed customer session in the `Authorization: Bearer` header. The
gateway derives the customer ID from that session; request JSON cannot select
another customer. The LangGraph supervisor routes technical questions to the
evidence-gated answer service, order questions to customer-scoped logistics,
and billing, mixed, unknown, or explicit-human requests to escalation. Service
tokens stay on the backend. The response omits internal handoff context.

No public session issuer or login flow exists yet, so this endpoint is only
usable with a session issued by a trusted integration. This message route has no
checkpointer or conversation history; each message runs independently.
Escalation is a response, not a persisted ticket. No billing action executes.

`POST /v1/support/messages/stream` accepts the same body and signed customer
session, returning `text/event-stream` for clients using a streaming `fetch`
request. Events have an `event:` name and JSON `data:` payload. The graph emits
`routing`; technical or fulfillment branches emit `tool_started` and
`tool_finished`; grounded technical results also emit `retrieval` with evidence.
Escalations emit `escalated` with a reason. Every successful stream ends with
`completed`, containing the same typed response as the non-streaming route.
An unexpected backend failure ends with a sanitized `error` event instead.
These are node-level events, not token-by-token text. The stream is not cached
and never includes customer IDs, raw handoff state, or service credentials.

`POST /v1/support/billing-requests` requires the same signed customer session
and a structured body with a UUID `conversation_id`, `action` (`refund` or
`cancellation`), UUID `target_id`, `reason`, and `amount_cents` only for refunds.
The agent binds the customer ID from the session, sends a request-only CRM token,
and derives a stable idempotency key from the conversation ID. If CRM accepts a
pending request, the billing graph interrupts and returns `approval_required`
with its request ID. While pending, a repeat of the same customer, conversation,
and payload returns the existing interrupt; a changed payload returns 409. CRM policy can
deny the request, in which case the workflow escalates without an interrupt.
PostgreSQL stores the checkpoint, including the action reason, so use synthetic
or appropriately governed data. After a decision, a repeated identical customer
request returns `executed` or `rejected` with the saved outcome.

`POST /v1/admin/customers/{customer_id}/conversations/{conversation_id}/actions/{action}/{request_id}/decision`
requires a signed administrator session and `{"decision":"approve"}` or
`{"decision":"reject"}`. The agent checks that the IDs match a paused checkpoint,
persists the decision through CRM using the administrator session, and resumes
only that thread. Rejection never calls execution. Approval calls the CRM mock
execution endpoint, which checks the approved status, customer and conversation
IDs, eligibility, and idempotency under row locks. An identical decision retry
returns the saved result; a conflicting decision returns 409. If execution
cannot complete, the route fails closed and a retry may resume the checkpoint.
There is no public admin or customer login issuer yet; trusted integrations
must issue sessions. Do not expose internal CRM credentials to the browser.

`GET /v1/admin/actions?offset=0&limit=50` requires the same administrator
session. The CRM supplies bounded pending/approved action details; the agent
returns only requests whose customer, conversation, payload, request ID, and
action match a paused graph checkpoint. The response is not cached and carries
`items`, `offset`, and `has_more`. CRM requests without a matching checkpoint
are not reviewable here. The underlying CRM review route is also admin-only.

### Internal CRM reads

`GET /internal/customers/{customer_id}/account` requires
`Authorization: Bearer <CRM_ACCOUNT_READ_TOKEN>` (or the billing token). It
returns account status and subscription summaries. `GET
/internal/customers/{customer_id}/billing` requires the distinct
`CRM_BILLING_READ_TOKEN` and returns scoped invoice/payment summaries, including
refundable minor-unit amounts. Neither route returns payment provider references.
Missing or invalid tokens fail closed. These are service-to-service contracts;
the future agent gateway must resolve the authenticated customer before passing
an ID. Customer-facing sessions must never receive any service token.

### Internal action requests

`POST /internal/customers/{customer_id}/refund-requests` accepts a payment ID,
positive minor-unit amount, reason, and conversation ID. `POST
/internal/customers/{customer_id}/cancellation-requests` accepts a subscription
ID, reason, and conversation ID. Both require `Authorization: Bearer
<CRM_BILLING_REQUEST_TOKEN>` and an `Idempotency-Key` header (8-100 safe
characters). The request credential is distinct from the read credentials.

The CRM checks customer ownership and deterministic eligibility, writes a
`pending` request and audit event in one transaction, and returns `201` for a new
request or `200` for an identical retry. Reusing a key with different data, or
opening a second action on the same target, returns `409`. Ineligible attempts
are audited and return `409`; unknown or other-customer resources return `404`.
These request endpoints do not refund a payment or cancel a subscription.

`POST /internal/action-requests/{refund|cancellation}/{request_id}/decision`
accepts `{"decision":"approve"}` or `{"decision":"reject"}` with a short-lived
signed administrator session in `Authorization: Bearer`. The administrator ID
comes from the verified session, never request JSON. Only a pending request may
transition; a matching retry returns the existing decision without a second
audit event, while a conflicting retry returns `409`. The request row is locked
during the decision and its audit insert in PostgreSQL. Neither decision
changes a payment balance or subscription. `admin_identity.issue_admin_session`
is an internal signing primitive, not a public login route; a trusted admin
login flow is still required. An identical approval retry after execution
returns the executed result without another decision audit event.

`POST /internal/customers/{customer_id}/action-requests/{refund|cancellation}/{request_id}/execute`
requires `Authorization: Bearer <CRM_BILLING_EXECUTE_TOKEN>` and the original
`conversation_id`. The execute credential is distinct from the read, request,
and administrator credentials and is supplied to CRM and the server-side agent
only for the authenticated resume path.
The CRM returns `404` for a wrong customer or conversation, rejects pending or
rejected requests, rechecks account and target eligibility, and locks the
approved request and target before a single transaction updates the local mock
ledger and writes its execution audit event. A retry of an executed request
returns the same result without another mutation or audit event. A full refund
marks the payment and invoice refunded; a partial refund leaves their existing
charged/paid status and updates the refundable balance. Cancellation is
immediate in the local mock. No real payment provider is contacted. The graph
calls this endpoint only after an administrator decision has been persisted and
the same thread resumes.

### Internal logistics reads

`GET /internal/customers/{customer_id}/orders` lists only that customer's
synthetic orders. `GET /internal/customers/{customer_id}/orders/{order_id}/tracking`
returns the order summary and ordered tracking events. Both require
`Authorization: Bearer <LOGISTICS_ORDER_READ_TOKEN>`. Missing or wrong tokens
return `403`; missing token configuration returns `503`. Unknown and other-customer
orders both return `404`. The gateway must authenticate the customer and bind its
ID before calling either endpoint; the service token must stay on the server.

Fixtures share the CRM seed's deterministic customer IDs. Customer 0 has an
in-transit order 0 and a delivery-exception order 2; customer 1 has delivered
order 1. For repeatable failure tests, tracking order IDs 90, 91, and 92 for
customer 0 return `429` (with `Retry-After: 2`), `503`, and `504` respectively.
The `504` is an emulated upstream timeout response, not a network delay. Obtain
the IDs with `support_system.fixture_ids.fixture_id("customer"|"order", number)`.
These fixtures do not appear in order lists. All logistics timestamps are fixed
test data, not live carrier updates.
