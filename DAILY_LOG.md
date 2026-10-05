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

## 2026-10-02 - Logistics order and tracking contract

- Goal and delivered behavior: Added scoped internal order-list and tracking reads with a dedicated logistics service token, shared deterministic customer IDs, three normal order histories, typed responses, and repeatable 403/404/429/503/504 cases. The 504 case emulates an upstream timeout response; no live carrier call or actual network timeout is involved.
- Decisions recorded in `DECISIONS.md`: Scope deterministic logistics reads by customer.
- Checks run and results: `uv sync --extra dev --frozen` passed; `ruff check .` passed; initial `pytest -q` passed 27 tests with 4 PostgreSQL tests skipped. A checkout-local PostgreSQL 17 instance then ran `alembic upgrade head`, seeded 37 synthetic customers, and passed `alembic check` with no schema drift; `RUN_POSTGRES_TESTS=1 pytest -q` passed all 31 tests with one upstream Starlette TestClient deprecation warning. `docker compose config --quiet` and `git diff --check` passed. The temporary PostgreSQL instance was stopped.
- Commit: `54af95e095c68dfab592602a4dea06b312bfdf31` (Add scoped logistics tracking API).
- Open risks or blockers: Docker Desktop's Linux engine pipe is absent, so full Compose boot and pgvector initialization remain unverified. The gateway still must authenticate and bind customer identity before using the logistics token. The 504 fixture covers an HTTP response, not a real client timeout.
- Next milestone: Build the support article corpus, pgvector indexing, evidence-bearing retrieval, and low-confidence escalation gate; recheck Compose boot when Docker becomes available.

## 2026-10-03 - Technical evidence retrieval

- Goal and delivered behavior: Added 20 versioned synthetic support articles, idempotent article seeding, a pgvector HNSW cosine schema/index migration, deterministic query embeddings, an internal token-guarded technical answer endpoint, article-ID citations and evidence metadata, and a low-confidence escalation result with no answer. Agent readiness now requires seeded knowledge. CI uses a pgvector PostgreSQL image and exercises the article seed twice.
- Decisions recorded in `DECISIONS.md`: Gate technical answers on versioned evidence. The lexical hash embedding avoids an external model credential but has limited paraphrase coverage; its fixed threshold is not a calibrated probability.
- Checks run and results: `uv lock`, `uv sync --extra dev --frozen`, and `ruff check .` passed. `pytest -q` passed 32 tests, skipped 5 PostgreSQL tests, and reported one upstream Starlette TestClient deprecation warning. Tests include all 20 bundled known questions, weak/adversarial escalation, token denial, and response serialization. `uv build --wheel` passed and the built wheel contains `support_articles.json`. `docker compose config --quiet`, `git diff --check`, and offline `alembic upgrade head --sql` passed; generated SQL includes the vector extension, `VECTOR(256)` column, and HNSW cosine index. Docker Desktop's Linux engine pipe remained unavailable after startup attempts, and local PostgreSQL 17 lacks pgvector, so migration execution, idempotent article seed, vector query, `alembic check`, full PostgreSQL tests, and Compose boot were not validated locally.
- Commit: `a001cc7392324dbd547171692260776845071907` (Add evidence-gated technical retrieval).
- Open risks or blockers: Local Compose boot remains unverified because Docker Desktop's Linux engine pipe is absent. The app has no authenticated customer chat, graph orchestration, or administrator approval flow yet. The articles describe a synthetic demo help center.
- Next milestone: Build LangGraph state, routing, and scoped technical/fulfillment/escalation nodes; recheck local Compose boot when Docker becomes available.
- CI follow-up: The prior logistics commit's CI had passed 31 tests but failed `docker compose config --quiet` because `LOGISTICS_ORDER_READ_TOKEN` was absent from the workflow environment. Commit `e1b6109397104a932f1fc870acdef79d5c6f19e0` supplied CI-only logistics and technical token placeholders. CI run `37097657165` then passed migration/rollback/re-migration on the pgvector image, seeded CRM customers 37 then 0 and support articles 20 then 0, found no Alembic drift, passed 37 tests with one upstream warning, and passed Compose config. This validates the pgvector migration and query path in CI, not a local full Compose boot.

## 2026-10-03 - Scoped workflow foundation

- Goal and delivered behavior: Added short-lived signed customer-session verification, server-bound customer state construction, narrow technical/logistics HTTP adapters with ownership validation and sanitized failure codes, and deterministic supervisor, technical, fulfillment, and escalation node functions. The supervisor escalates billing, mixed, unknown, and explicit-human requests. Technical output requires evidence; fulfillment uses the verified customer ID. No public conversation route, LangGraph assembly, checkpointer, or financial execution was enabled.
- Decisions recorded in `DECISIONS.md`: Bind identity before workflow tools.
- Checks run and results: `uv sync --extra dev --frozen --offline`, `ruff check .`, `docker compose config --quiet`, and `git diff --check` passed. `pytest -q` passed 59 tests with 5 PostgreSQL tests skipped and one upstream TestClient warning. New tests cover signature, expiration, malformed sessions, scope-specific credentials, cross-customer response rejection, conservative routing, evidence gating, and fail-closed service errors. The Docker engine pipe was absent, so local Compose boot could not run.
- Commit: `620ea0e3d1f1d53cfbe8c7ceee339e184b119fde` (Add scoped workflow foundations), local only at this point.
- Open risks or blockers: The sandbox denied outbound GitHub connections on port 443, so fetch and push failed. `uv add langgraph` failed because the sandbox denied the PyPI socket (Windows error 10013); no LangGraph dependency or graph was committed. The Day 6 graph gate remains open, and CI/full PostgreSQL checks for this commit have not run. No session issuer or authenticated public route exists yet.
- Next milestone: Restore dependency and GitHub access, install and lock LangGraph, wire these nodes into a compiled graph and authenticated endpoint, then run routing/tool and PostgreSQL CI checks before claiming the gate.

## 2026-10-03 - Authenticated workflow graph

- Goal and delivered behavior: Pushed the previously local-only workflow foundation commits, then installed and locked LangGraph, compiled the scoped supervisor/technical/fulfillment/escalation graph, and added a signed-session-only message endpoint. Technical answers require evidence, fulfillment uses the verified customer ID, and billing or ambiguous messages escalate. The endpoint omits internal handoff data; no customer login issuer, checkpoint, persistent ticket, or financial execution exists.
- Decisions recorded in `DECISIONS.md`: Compile a stateless, scoped LangGraph before approval persistence.
- Checks run and results: Frozen offline `uv sync`, Ruff, and `git diff --check` passed; `uv run --offline pytest -q` passed 64 tests with 5 PostgreSQL skips. `docker compose config --quiet` passed with placeholder credentials, though Docker could not read the user-level config under the current sandbox. A wheel build passed to a fresh checkout-local `.tmp` directory; writing over the existing ignored `dist` wheel was denied. Full container boot and local pgvector checks remain unverified.
- Commit: `554d0dba49245ae230019df2828ae4f08d05f5bb` (Wire authenticated support workflow), pushed directly to `origin/main`.
- CI follow-up: GitHub Actions run `37135500016` passed the backend job, including frozen dependency install, lint, PostgreSQL/pgvector migration and seed checks, the full test step, and Compose validation. This does not replace a local full Compose boot.
- Open risks or blockers: Session issuance, conversation persistence, durable handoff, billing approval/resume, and rate limiting remain to be built. The Docker Linux engine remains unavailable locally.
- Next milestone: Add persisted billing interrupt, administrator approval/rejection, same-thread resume, and exactly-once CRM execution before opening sensitive actions.

## 2026-10-04 - Administrator decision boundary

- Goal and delivered behavior: Added a signed administrator session verifier separate from customer sessions and an internal CRM approve/reject endpoint for pending refund and cancellation requests. PostgreSQL locks the request row while persisting the decision and administrator-attributed audit event. Matching retries return the existing decision; contradictory decisions fail. Payment balances and subscription status remain unchanged. This is a partial billing gate, not an executable refund/cancellation workflow.
- Decisions recorded in `DECISIONS.md`: Persist administrator decisions before financial execution.
- Checks run and results: `uv sync --extra dev --frozen --offline`, `ruff check .`, changed-file `ruff format --check`, `docker compose config --quiet` with validation-only credentials, and `git diff --cached --check` passed. Local `pytest -q` passed 67 tests with 6 PostgreSQL tests skipped because a pgvector-backed database was not running. GitHub Actions run `37165919092` passed 73 tests on pgvector PostgreSQL, including decision persistence, replay, audit uniqueness, and unchanged balance; migration/drift and Compose validation passed. Full Compose boot was not run locally.
- Commit: `00d8e952dea07a73ab4585254d2f8f31a9934b41` (Add guarded administrator decisions).
- Open risks or blockers: No trusted admin login flow or public admin UI exists. The agent still escalates billing; it does not create a checkpointed pending action, resume the same thread, or execute approved actions. Concurrent row-lock behavior and full Compose boot remain unverified locally; CI covered the PostgreSQL decision path but not simultaneous reviewers.
- Next milestone: Persist billing workflow checkpoints, create requests from customer-scoped tools, interrupt for review, and resume the same thread; open execution only after idempotent backend mutation and adversarial retry tests pass.

## 2026-10-04 - Approved-only mock execution

- Goal and delivered behavior: Added a CRM-only execute credential and endpoint for approved refund/cancellation requests. The CRM binds execution to customer and conversation IDs, rechecks current eligibility under row locks, updates the local mock payment/subscription state and audit event in one transaction, and returns the same result on replay. The agent service does not receive the execute credential; no customer-facing billing workflow or real payment-provider call is enabled.
- Decisions recorded in `DECISIONS.md`: Make mock execution an approved-only replay-safe CRM operation.
- Checks run and results: `uv sync --extra dev --frozen --offline`, `ruff check .`, changed-file `ruff format --check`, `docker compose config --quiet` with validation-only credentials, and `git diff --cached --check` passed. Local `pytest -q` passed 74 tests with 7 PostgreSQL tests skipped. GitHub Actions run `37177821279` passed 81 tests on PostgreSQL/pgvector, including simultaneous execution retries with one ledger update and one execution audit, plus migration/drift and Compose validation. Full local Compose boot was not run.
- Commit: `6e0484d9ab1d7fda14ec7513f9b40bc7693bfc80` (Protect approved CRM execution), pushed directly to `origin/main`.
- Open risks or blockers: The graph still escalates billing and has no durable checkpoint, human interrupt, or same-thread resume. The isolated execution credential must not be given to agent-api until those gates and end-to-end tests pass. Local full Compose boot remains unverified.
- Next milestone: Add a customer-scoped billing node with durable checkpoint/interrupt, verify administrator decision and same-thread resume, then connect the already guarded CRM execution tool with replay tests.

## 2026-10-05 - Customer billing request checkpoint

- Goal and delivered behavior: Added an authenticated, structured refund/cancellation request route. The fifth graph node sends a customer-scoped, idempotent request to CRM and persists a PostgreSQL interrupt containing the pending request ID. Same-payload retries return the saved interrupt; changed-payload retries conflict. The agent has only the request credential. An unverified low-level resume escalates; no agent execution or public admin-resume route exists.
- Decisions recorded in `DECISIONS.md`: Persist a pending billing interrupt without opening execution.
- Checks run and results: A fresh Python 3.12 environment and cache on D: passed frozen `uv sync`, `ruff check .`, changed-file `ruff format --check`, `git diff --cached --check`, and `docker compose config --quiet` with validation-only credentials. Local `pytest -q` passed 79 tests and skipped 8 PostgreSQL tests because the local Docker engine was unavailable. GitHub Actions run `37248965026` passed 87 tests, including PostgreSQL checkpoint reconnection, plus migration/seed, lint, and Compose validation. Full local Compose boot remains unverified.
- Commit: `ce854c32765312cc64f6da2a53cdd2fc7a9e25ca` (Persist customer billing requests for review), pushed directly to `origin/main` and remote SHA verified.
- Open risks or blockers: Day 7 is partial: administrator decision is not yet connected to same-thread resume, and execution remains closed. No public login/session issuer exists. The original checkout-local uv cache and virtual environment were unreadable (Windows error 1392); D: development storage allowed checks without deleting those paths. A K: scan reported RAW while volume information reported NTFS, so the storage condition is unresolved. Docker's Linux engine remains unavailable locally.
- Next milestone: Authenticate an administrator decision, resume the exact paused thread, and wire the guarded CRM execution adapter with rejection, replay, and cross-customer tests before exposing the admin workflow.

## 2026-10-05 - Administrator decision and same-thread resume

- Goal and delivered behavior: Added an admin-session-protected decision route that matches customer, conversation, action, and request IDs to a paused checkpoint before calling CRM. Approval resumes that thread and invokes the guarded, idempotent mock execution endpoint; rejection resumes without execution. The agent validates CRM responses, identical decisions replay, contradictory decisions fail, and execution failures leave the interrupt retryable. A matching approval retry after execution returns the existing CRM result. Customer retries show the final outcome.
- Decisions recorded in `DECISIONS.md`: Bind admin decisions to the paused graph thread. The execute token is now available to the server-side agent but not the browser, while CRM remains the authorization and idempotency boundary.
- Checks run and results: The D: Python 3.12 environment passed `ruff check .`, changed-file `ruff format --check`, `git diff --cached --check`, and `docker compose config --quiet` with validation-only credentials. Local `pytest -q` passed 83 tests and skipped 11 PostgreSQL tests. GitHub Actions run `37264974930` passed all 94 tests, including an agent-to-CRM PostgreSQL refund/approval/execution test, checkpoint resume across connections, CRM replay, migration/seed, lint, and Compose validation. `docker info` could not reach the local Linux engine, so full local Compose boot remains unverified.
- Commit: `59ed83139c83a5b777f102d9f0244cde8a27e6f8` (Resume approved billing actions), pushed directly to `origin/main` and remote SHA verified.
- Open risks or blockers: The local mock billing approval/resume gate passes, but no real payment provider or trusted public customer/admin login exists. There is no admin queue/UI, customer chat UI, SSE progress, rate limiting, or checkpoint retention policy yet. Concurrent graph resumes are not separately load-tested; CRM's concurrent execution path is covered. The original K: cache/venv storage issue and unavailable local Docker engine remain unresolved.
- Next milestone: Build the minimal Next.js customer chat and admin approval view with SSE progress, then test a complete browser workflow against the local services without exposing service credentials.
