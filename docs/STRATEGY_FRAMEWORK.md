# Strategy Framework

A strategy is a versioned specification, not a prompt.

Required fields:
- strategy_id
- version
- universe
- timeframe(s)
- entry conditions
- exit conditions
- stop/invalidation
- position sizing
- capital allocation
- transaction-cost model
- liquidity constraints
- session constraints
- cooldown rules
- max concurrent positions
- max daily trades
- risk limits
- benchmark

## Strategy categories to support

Start with:
1. trend-following
2. moving-average regimes
3. momentum
4. mean reversion
5. breakout/volatility expansion
6. pairs/relative-value research
7. event-driven rules
8. portfolio rebalancing

Avoid building dozens of strategies initially. Build a framework where a new strategy can be registered and tested without modifying core execution code.
