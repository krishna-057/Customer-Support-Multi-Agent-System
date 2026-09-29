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
