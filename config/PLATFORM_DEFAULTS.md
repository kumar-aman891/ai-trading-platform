# Platform Defaults

Mode at first startup: PAPER

LIVE trading: disabled — no LIVE execution code exists in this build (ADR-005, ADR-008)

Kill switches at first startup (see ADR-007 for the full taxonomy):
- GLOBAL_LIVE: engaged (scopes all LIVE order paths; not clearable — no route exists)
- LIVE_ACCOUNT: engaged
- PAPER: disengaged (so paper trading works out of the box)
- STRATEGY:{id} / INSTRUMENT:{id} / API_EXECUTION: disengaged

Default AI tool permissions:
- read market data: allowed
- read portfolio: allowed
- research: allowed
- propose paper trade: allowed (creates a TradeProposal only)
- propose live trade: not available — no live route or live-facing tool exists
- execute any trade: never. The AI cannot execute orders under any
  condition, PAPER or LIVE. It may submit a TradeProposal; only the
  deterministic risk engine and, downstream of an APPROVED decision, the
  execution gateway may turn that into an order (ADR-008). This is a
  structural boundary, not a permission flag that could be toggled.
- modify risk limits: blocked for AI — no route mutates risk configuration
  at all; it is migration-seeded and versioned (see docs/RISK_AND_GUARDRAILS.md)

Default data freshness policy:
- research: configurable, tolerate stale data with warning
- signal generation: strict freshness threshold
- live execution: strict freshness threshold; reject when unknown/stale

Default logging:
- structured JSON
- no secrets
- correlation IDs required
