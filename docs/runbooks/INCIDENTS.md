# Incident Handling

## Unknown order status

1. Stop new orders for the affected strategy/account.
2. Query broker order history and trades.
3. Reconcile positions.
4. Do not blindly retry an order.
5. Record incident and correlation IDs.

## Market data stale/disconnected

1. Stop new live entries.
2. Keep monitoring/reconciliation active if possible.
3. Attempt controlled reconnect.
4. Verify fresh ticks before re-arming.

## Risk service unavailable

Fail closed: no new live orders.

## Kill switch

Immediately block new orders, alert the user, and reconcile current state.
