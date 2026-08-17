# Claude Code Starting Prompt

You are building the AI Trading Platform from this specification repository.

First, do not write feature code. Read all repository documents, identify contradictions, and produce an implementation plan with milestones and dependency ordering.

Then create the project skeleton only, using the architecture and contracts in these documents. Do not implement live trading until the paper trading path, deterministic risk layer, audit logging, and tests exist.

Required first deliverables:
1. monorepo structure for frontend/backend/domain/workers/execution/research/tests/docs.
2. environment/config abstraction.
3. PostgreSQL schema/migrations for the conceptual entities in docs/SCHEMAS.md.
4. domain interfaces for broker, market data, news, fundamentals, storage, LLM and execution.
5. health/readiness endpoints.
6. event/audit model.
7. paper-trading stub adapter.
8. deterministic risk engine interface with conservative reject-by-default behavior.
9. broker adapter interface with a Kite implementation planned behind a feature flag.
10. initial React routes and navigation shell, with LIVE route clearly marked and disabled by default.

Before writing each major module, state the relevant architectural contract and tests that will prove it works.

Never place secrets in source control.
Never use an LLM as the final live trade permission authority.
Never connect a newly created feature to live execution by default.
