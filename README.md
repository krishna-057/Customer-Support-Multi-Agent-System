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

The foundation provides three FastAPI service shells, shared health contracts, a
PostgreSQL 17/pgvector Compose service, a dependency lock, and Python smoke tests.
Business endpoints are deliberately absent until the CRM authorization and write
safety gates are implemented. See [PLAN.md](PLAN.md) for the sequence and
[DECISIONS.md](DECISIONS.md) for design tradeoffs.

## Local development

Use Python 3.12, uv, and Docker Compose. Keep uv's cache and local data in this
checkout. In PowerShell:

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.cache/uv'
uv sync --extra dev --frozen
uv run ruff check .
uv run pytest -q
```

Copy `.env.example` to ignored `.env` and replace the sample password with a
unique local value. Then run `docker compose up --build`. PostgreSQL data is
bind-mounted at `./data/postgres`; the pgvector extension is created when the
database is first initialized. Only loopback ports are exposed: agent API 8000,
CRM API 8001, logistics API 8002, and PostgreSQL 54329 by default.

Each service exposes `/health/live` and `/health/ready`. CRM readiness queries
PostgreSQL and returns 503 without a working database. The other readiness
endpoints currently report process readiness because those service shells have
no downstream dependencies yet. The image installs pinned runtime dependencies
from `requirements.txt`, exported from `uv.lock`.
