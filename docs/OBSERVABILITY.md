# Observability and AI Audit

## Every important action gets an event

Minimum fields:
- event_id
- correlation_id
- timestamp
- actor_type (user/agent/system/broker)
- actor_id
- action
- mode (PAPER/LIVE)
- strategy_id/version if applicable
- symbol/instrument token if applicable
- source references
- input snapshot hash
- decision/result
- risk rule IDs
- broker order ID if applicable
- error code/class if failed

## AI-specific trace

Record:
- model/provider identifier
- prompt/template version
- tool names called
- tool-call latency
- token usage where available
- structured output validation result
- evidence references
- final recommendation
- whether recommendation was acted upon

Do not store sensitive raw prompts if they contain private account data; store redacted content or a content hash plus safe structured metadata.

## Operational metrics

- market data latency
- data gaps
- WebSocket reconnects
- API errors by code
- order rejects
- execution latency
- slippage
- fill ratio
- duplicate order prevention count
- risk rejects
- daily P&L
- exposure
- drawdown
- AI tool error rate
- model latency/cost
- token usage per workflow

## Alerts

High priority:
- position mismatch
- unknown order status
- broker connectivity loss during active strategy
- daily loss limit breach
- kill switch activation
- secret leakage detection
- repeated API 429s
- stale market data while live strategy is armed
