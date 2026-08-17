# Architecture Rules

- Keep domain logic independent of FastAPI, React, MCP, and broker SDK details.
- Use ports/adapters boundaries for broker, market data, news, fundamentals, LLM, storage, notifications, and clock/calendar.
- Keep execution idempotent.
- Use UTC internally; store exchange-local timestamps and session IDs explicitly.
- All external IDs must be stored together with provider/source metadata.
- Prefer append-only event records for decisions, orders, fills, risk decisions, and AI tool calls.
