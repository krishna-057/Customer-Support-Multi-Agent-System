# Curated single-turn evaluation

Run `python -m evals.run` from the repository root. CI also runs each case as a
separate pytest parameter. The 100 hand-labeled prompts in `cases.jsonl` contain
20 technical, 20 fulfillment, 20 ambiguous, 20 adversarial billing, and 20
simulated service-failure scenarios.

## 2026-10-08 baseline

- First pass: 98/100. Two fulfillment prompts using the plural `orders` were
  escalated because the lexical supervisor recognized only `order`.
- After adding `orders`: 100/100 route and terminal-state checks passed.
- Technical success cases asserted an evidence-bearing cited response; weak
  evidence cases escalated. Fulfillment cases asserted customer-bound tool
  arguments. All 20 unsafe billing prompts stayed on the escalation path with
  no billing tool or execution result.
- Local in-process graph latency for this run: p50 2.59 ms, p95 3.01 ms. This
  excludes HTTP, PostgreSQL, network tools, streaming, and browser rendering.

These are 100 **single-turn synthetic scenarios**, not 100 multi-turn customer
conversations. Technical and logistics tools are deterministic fixtures; the
corpus tests routing and safety behavior, not retrieval recall against the
article database, actual CRM mutation, real service outages, or production
latency/cost. Existing PostgreSQL-backed CI tests cover separate CRM and billing
contracts. Full backend-connected browser evaluation, tracing/redaction,
rate-limit tests, and a trusted login flow remain open before a public V1 claim.
