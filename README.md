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

## Current implementation

The backend provides three FastAPI services, PostgreSQL 17/pgvector Compose,
versioned CRM migrations, deterministic synthetic fixtures, guarded account and
billing reads, and approval-bound refund/cancellation request creation. Financial
execution routes remain closed until administrator approval, persisted workflow
resume, and duplicate-execution protection are implemented.
See [PLAN.md](PLAN.md) for sequencing and [DECISIONS.md](DECISIONS.md) for tradeoffs.

## Local development

Use Python 3.12, uv, and Docker Compose. Keep uv's cache and local data in this
checkout. In PowerShell:

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.cache/uv'
uv sync --extra dev --frozen
uv run ruff check .
uv run pytest -q
```

Copy `.env.example` to ignored `.env` and replace the sample password and three
service tokens with unique, distinct local values. Then run `docker compose up
--build`. The CRM container applies migrations and inserts 37 synthetic
customers on startup. PostgreSQL data is
bind-mounted at `./data/postgres`; the pgvector extension is created when the
database is first initialized. Only loopback ports are exposed: agent API 8000,
CRM API 8001, logistics API 8002, and PostgreSQL 54329 by default.

Each service exposes `/health/live` and `/health/ready`. CRM readiness queries
PostgreSQL and returns 503 without a working database. The other readiness
endpoints currently report process readiness because those service shells have
no downstream dependencies yet. The image installs pinned runtime dependencies
from `requirements.txt`, exported from `uv.lock`.

For a separately running PostgreSQL instance, set `DB_HOST`, `DB_PORT`,
`DB_NAME`, `DB_USER`, and `DB_PASSWORD`, then run `uv run alembic upgrade head`
and `uv run python -m support_system.seed_crm`. Re-running the seed is safe and
reports zero new customers. `uv run alembic check` detects schema drift.

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
These endpoints do not refund a payment or cancel a subscription. The future
approval workflow must authorize an administrator, persist the decision, resume
the same conversation, and execute exactly once before those state changes are
available.
