# Daily log

Record implementation progress by date:

## YYYY-MM-DD - Milestone

- Goal and delivered behavior:
- Decisions recorded in `DECISIONS.md`:
- Checks run and results:
- Commit:
- Open risks or blockers:
- Next milestone:

## 2026-09-29 - Backend foundation

- Goal and delivered behavior: Added three FastAPI service shells, shared typed health responses, CRM database readiness, PostgreSQL/pgvector Compose setup, pinned dependencies, a CI workflow, and smoke tests. Sensitive and customer data routes are still closed.
- Decisions recorded in `DECISIONS.md`: Isolate business routes behind backend gates; keep local database files in the checkout.
- Checks run and results: `uv sync --extra dev --frozen` succeeded; `ruff check .` passed; `pytest -q` passed 9 tests (one upstream TestClient deprecation warning); `docker compose config --quiet` passed with a validation password. Direct Uvicorn smoke checks returned live 200 for all three services, ready 200 for agent/logistics, and expected ready 503 for CRM without a database. Docker engine was unavailable, so container boot and PostgreSQL extension initialization were not tested.
- Commit: Add backend service foundation (this entry's commit).
- Open risks or blockers: Day 1 Compose boot gate remains open until Docker can run; do not mark it passed. No CRM schema or business endpoints exist yet.
- Next milestone: Verify Compose boot, then implement CRM schema, migrations, seeded data, and scoped account read APIs.

## 2026-10-01 - CRM schema and guarded reads

- Goal and delivered behavior: Added eight versioned CRM tables, 25 synthetic baseline customers and 12 edge cases, repeatable seed data, scoped account and billing read endpoints, distinct internal service credentials, response contracts, and PostgreSQL integration tests. Financial write endpoints remain closed.
- Decisions recorded in `DECISIONS.md`: Gate CRM reads with distinct service credentials; seed repeatable synthetic CRM cases.
- Checks run and results: `uv sync --extra dev --frozen` passed; `ruff check .` passed; `pytest -q` passed 18 tests against a running local PostgreSQL 17 instance (one upstream TestClient deprecation warning); `alembic upgrade head`, `alembic downgrade base`, and re-upgrade passed; first seed inserted 37 customers and second inserted 0; `alembic check` found no drift; `docker compose config --quiet` and `git diff --check` passed.
- Commit: `72e47b74f598d7701d837d6e9220a1a9ce3dd452` (Add CRM schema and scoped read APIs).
- Open risks or blockers: Docker engine is unavailable, so full Compose boot and pgvector extension initialization remain unverified. Internal service credentials do not establish end-user identity; the agent gateway must bind an authenticated customer before calling these reads. CRM write authorization, policy, approval, idempotency, and audit behavior are not implemented.
- Next milestone: Implement CRM scoped write authorization and deterministic refund/cancellation request policy with idempotency and audit tests before enabling sensitive execution.

## 2026-10-01 - CRM action request gate

- Goal and delivered behavior: Added guarded refund and cancellation request creation with a distinct request credential, customer-owned target lookup, deterministic eligibility checks, bounded idempotency keys, atomic creation audit events, policy-denial audit events, and database uniqueness for competing open requests. Requests remain pending; payment balances and subscription states are unchanged.
- Decisions recorded in `DECISIONS.md`: Persist action requests without executing them.
- Checks run and results: `uv sync --extra dev --frozen` and `ruff check .` passed; local PostgreSQL 17 `alembic downgrade base` then `upgrade head` passed; seed inserted 37 customers then 0 on repeat; `alembic check` found no drift; `pytest -q` passed 25 tests (one upstream TestClient deprecation warning), including authorization, idempotent retries, competing requests, cross-customer IDs, policy denial, audit, and unchanged financial state; `docker compose config --quiet` and `git diff --check` passed. Docker engine connection failed because the Docker Desktop Linux pipe was absent, so container boot was not tested.
- Commit: `c7671381c2e81c5ffd33836913836906a2b7e122` (Add guarded CRM action requests).
- Open risks or blockers: Full Compose boot and pgvector initialization remain unverified without Docker. Customer identity binding, administrator approval/rejection, persisted same-thread resume, and exactly-once financial execution are later gates; no route currently performs a refund or cancellation.
- Next milestone: Build the logistics order/tracking contract with deterministic service-failure fixtures, while rechecking the open Compose boot gate.
