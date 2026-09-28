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
