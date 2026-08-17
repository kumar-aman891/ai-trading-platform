# Live Trading Rules

No LIVE order path may be added without:
- explicit live-mode guard
- account/segment permission check
- market-session check
- instrument eligibility check
- quantity/price/tick/lot validation
- cash/margin/position validation
- per-order risk limit
- daily loss limit
- position concentration limit
- strategy exposure limit
- order-rate/OPS limit
- duplicate-order/idempotency check
- kill-switch status check
- broker connectivity health check
- audit event before and after execution request
- broker acknowledgement and reconciliation

When any hard check is indeterminate, reject the order rather than guessing.
