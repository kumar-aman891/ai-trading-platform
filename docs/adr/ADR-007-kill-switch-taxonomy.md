# ADR-007: Kill-Switch Taxonomy and Scope

## Status
Accepted — Phase 1.

## Context
[docs/RISK_AND_GUARDRAILS.md](../RISK_AND_GUARDRAILS.md) names five kill
switches (global, live-account, strategy-level, instrument-level, API
execution) without defining "global" precisely, and
[config/PLATFORM_DEFAULTS.md](../../config/PLATFORM_DEFAULTS.md) sets both
`TRADING_MODE=PAPER` at first startup and the global kill switch to
"enabled until explicitly cleared." Read literally, if "global" scopes both
PAPER and LIVE, the platform would ship unable to paper trade on first run —
an odd default for a system that is supposed to default to safe/PAPER
operation.

## Decision
Six switches, each independently engageable/clearable, each fail-closed on
an unreadable state:

| Switch ID | Scope | Phase 1 default | Clearable in Phase 1 |
|---|---|---|---|
| `GLOBAL_LIVE` | all LIVE order paths | **ENGAGED** | No — no route exists to clear it |
| `LIVE_ACCOUNT` | one live broker account | ENGAGED | No |
| `PAPER` | all PAPER order paths | disengaged | Yes — `administrator` |
| `STRATEGY:{id}` | one strategy | disengaged | Yes — `administrator` |
| `INSTRUMENT:{id}` | one instrument | disengaged | Yes — `administrator` |
| `API_EXECUTION` | all programmatic order submission | disengaged | Yes — `administrator` |

`GLOBAL_LIVE` — not a switch named merely "global" — scopes LIVE only.
Engaging any switch blocks new orders in its scope immediately. Disengaging
requires the `administrator` role; engaging requires only `paper_trader` or
above, an intentional asymmetry (stopping is cheaper than starting).

Every transition is written to `core.kill_switch_history` and to
`audit.audit_events` (actor, reason, prior state) in one transaction.

State is read from `core.kill_switch_state` (source of truth) with a
short-TTL cache. If the state cannot be read for any reason — database
unreachable, cache unreachable, row missing, value unparseable — the policy
returns **ENGAGED**, not a default guess.

## Consequences
[docs/RISK_AND_GUARDRAILS.md](../RISK_AND_GUARDRAILS.md) and
[config/PLATFORM_DEFAULTS.md](../../config/PLATFORM_DEFAULTS.md) are updated
to reference this table rather than an undifferentiated "global kill
switch." The platform ships able to paper trade and structurally unable to
live trade — `GLOBAL_LIVE` engaged is redundant with the absence of any LIVE
execution code (ADR-005, ADR-008) but costs nothing to also assert.
