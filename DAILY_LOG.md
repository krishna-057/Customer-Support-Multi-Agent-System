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
