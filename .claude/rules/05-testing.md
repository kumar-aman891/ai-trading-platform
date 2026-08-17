# Testing Rules

Trading logic requires deterministic tests for:
- signal generation
- sizing
- order construction
- fees/slippage
- market hours
- split/dividend adjustments
- stop-loss and take-profit behavior
- partial fills
- duplicate events
- broker errors
- reconnects
- stale data
- kill switch
- session expiry
- race conditions

Any bug involving an order or P&L calculation gets a regression test before the fix is considered complete.
