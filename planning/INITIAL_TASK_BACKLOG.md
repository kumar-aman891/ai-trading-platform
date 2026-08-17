# Initial Task Backlog

## P0 — Safety/Foundation
- [ ] Repository structure
- [ ] Configuration and secrets abstraction
- [ ] PostgreSQL migrations
- [ ] Audit/event ledger
- [ ] Authentication/authorization
- [ ] LIVE/PAPER hard separation
- [ ] Risk engine skeleton
- [ ] Kill switch skeleton
- [ ] Structured logging and tracing

## P1 — Market Data
- [ ] Kite instrument master loader
- [ ] Historical OHLC adapter
- [ ] WebSocket market stream
- [ ] Data normalization
- [ ] Data quality checks
- [ ] Redis quote cache
- [ ] chart API

## P1 — Research
- [ ] pandas-ta-classic indicator service
- [ ] multi-timeframe analysis
- [ ] fundamentals provider abstraction
- [ ] news provider abstraction
- [ ] stock search/screener
- [ ] AI research orchestration

## P1 — Paper Trading
- [ ] StrategySpec model
- [ ] signal engine
- [ ] paper order book
- [ ] fill simulator
- [ ] P&L accounting
- [ ] fees/slippage model

## P2 — Backtesting
- [ ] event-driven engine
- [ ] walk-forward runner
- [ ] performance metrics
- [ ] sensitivity analysis
- [ ] trade-level attribution

## P2 — Live Safety
- [ ] broker session lifecycle
- [ ] order idempotency
- [ ] pre-trade risk checks
- [ ] broker execution gateway
- [ ] order reconciliation
- [ ] kill switch
- [ ] live activity console

## P3 — Advanced
- [ ] regime detection
- [ ] portfolio risk analytics
- [ ] strategy ensemble
- [ ] anomaly detection
- [ ] strategy health monitoring
