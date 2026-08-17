# Core Domain Schemas (Conceptual)

This document is the conceptual sketch. Concrete types, keys, indexes,
nullability, and enum vocabularies for every Phase 1 entity live in
[docs/schemas/](schemas/README.md), organized by PostgreSQL schema
(`core`, `audit`, `paper`) per ADR-005's isolation design. `live` is created
empty in Phase 1 (ADR-005 §5.4) and so has no entities of its own yet — it
will mirror `paper`'s tables when Phase 4 populates it.

`mode` is added below to every execution-path entity that was missing it —
`Signal`, `TradeProposal`, `RiskDecision`, `Fill` — so mode is explicit and
non-null everywhere an execution-path entity exists, per ADR-005. An
`AuditEvent` entity is added; it was referenced throughout
docs/OBSERVABILITY.md but omitted here.

## Instrument

instrument_id, provider, instrument_token, exchange, segment, symbol, name, expiry, strike, option_type, lot_size, tick_size, active_from, active_to.

## MarketBar

instrument_id, timeframe, event_time, open, high, low, close, volume, oi, source, fetched_at, adjustment_mode, quality_state.

## Signal

signal_id, strategy_id, strategy_version, instrument_id, mode, event_time, direction, strength, features_hash, rationale_reference.

## TradeProposal

proposal_id, mode, strategy_id/version, instrument_id, side, quantity, order_type, price, trigger_price, product, client_request_id, expected_risk, created_at, source_signal_id.

## RiskDecision

decision_id, mode, proposal_id, outcome, rule_results, risk_config_id, limit_snapshot_hash, decided_at.

## ApprovedOrderIntent

intent_id, mode, decision_id, proposal_id, canonical_payload, payload_hash, minted_at, expires_at. Minted only by the risk engine from an APPROVED RiskDecision; single-use (ADR-008). See [docs/schemas/order_intent.md](schemas/order_intent.md).

## Order

internal_order_id, broker_order_id, mode, proposal_id, intent_id, idempotency_key, status, submitted_at, acknowledged_at, last_update_at.

## Fill

fill_id, internal_order_id, mode, broker_trade_id, quantity, price, timestamp, fees, taxes, source.

## Position

position_id, instrument_id, mode, quantity, average_price, realized_pnl, unrealized_pnl, updated_at.

## AuditEvent

event_id, correlation_id, occurred_at, recorded_at, actor_type, actor_id, action, mode, strategy_id, strategy_version, instrument_id, source_refs, input_hash, decision, risk_rule_ids, broker_order_id, broker_provider, error_code, error_class, payload. Append-only (ADR-010). See [docs/schemas/audit_event.md](schemas/audit_event.md).

## AIEvent

event_id, correlation_id, model, prompt_version, tool_name, input_reference, output_reference, token_usage, latency_ms, result_status, created_at. Not created in Phase 1 — no LLM provider is integrated yet; documented here to fix its shape ahead of the AI plane.
