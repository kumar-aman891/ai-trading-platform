# Risk, Guardrails and Hard Exits

## Hard kill switch

Implement six independent kill switches, scoped per ADR-007
(docs/adr/ADR-007-kill-switch-taxonomy.md):

| Switch ID | Scope | Default |
|---|---|---|
| `GLOBAL_LIVE` | all LIVE order paths | engaged |
| `LIVE_ACCOUNT` | one live broker account | engaged |
| `PAPER` | all PAPER order paths | disengaged |
| `STRATEGY:{id}` | one strategy | disengaged |
| `INSTRUMENT:{id}` | one instrument | disengaged |
| `API_EXECUTION` | all programmatic order submission | disengaged |

`GLOBAL_LIVE` scopes LIVE only — it does not block PAPER trading. A separate
`PAPER` switch governs paper trading independently, so the two modes can be
halted without one silently gating the other.

A kill switch must block new orders immediately. State reads that fail for
any reason (database unreachable, cache unreachable, row missing, value
unparseable) must resolve to ENGAGED, never to a default guess. It should
optionally support a controlled flattening procedure, but flattening itself
must pass risk checks.

## Mandatory pre-trade checks

- mode == LIVE
- live trading enabled by user
- broker session valid
- exchange/segment open
- instrument valid and tradeable
- order quantity respects lot/tick rules
- sufficient available funds/margin
- position limits
- symbol concentration limit
- sector/portfolio concentration limit
- per-trade max loss
- daily realized + unrealized loss limit
- strategy max allocation
- open order duplication check
- maximum order notional
- maximum turnover/day
- order frequency / OPS limit
- price sanity vs latest canonical quote
- data freshness threshold
- spread/liquidity threshold
- circuit/price-band sanity where available
- news/event blackout rules where configured
- broker health check
- system clock health

## Hard exits

Reject trades when:
- market data is stale
- data sources disagree beyond a configured tolerance
- broker session is expired/uncertain
- risk state is unavailable
- portfolio reconciliation is stale
- duplicate execution risk cannot be ruled out
- required stop/invalidation information is missing
- system is in degraded mode
- daily loss limit breached
- GLOBAL_LIVE kill switch engaged (or, for a PAPER proposal, the PAPER kill switch engaged)

## Post-trade controls

- reconcile open orders
- reconcile fills
- reconcile positions
- compute cash/margin change
- verify expected vs broker result
- raise incident on mismatch

## Strategy-level protection

Each strategy must have:
- max capital
- max concurrent positions
- max daily loss
- max drawdown
- maximum position size
- max trade frequency
- market/session eligibility
- allowed instruments
- allowed order types
- explicit version

Risk configuration is immutable per run and versioned.
