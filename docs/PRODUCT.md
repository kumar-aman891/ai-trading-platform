# Product Specification

## Dashboard

Show:
- portfolio value
- cash/margin
- realized/unrealized P&L
- day P&L
- exposure
- drawdown
- active strategies
- current market regime
- open orders
- risk alerts
- AI activity summary

## Live Trading

- explicit LIVE banner
- arm/disarm control
- strategy status
- proposed orders
- risk approval/rejection explanation
- broker confirmations
- open positions
- kill switch

## Paper Trading

- simulated capital
- fill simulator status
- same strategy/risk interfaces as live where possible
- separate order ledger

## Technical Analysis

- multi-timeframe candles
- volume
- indicators
- patterns
- trend/regime
- support/resistance
- volatility
- signal history

## Fundamentals

- revenue/earnings growth
- margins
- ROE/ROCE where applicable
- leverage
- cash flow
- valuation multiples
- growth vs valuation
- peer comparison
- key filing/event history

## News / Events

- latest headlines
- corporate announcements
- earnings/events calendar
- materiality classification
- source and timestamp

## Search / Screener

Filters for:
- price
- market cap
- liquidity
- momentum
- volatility
- fundamentals
- valuation
- volume anomalies
- technical signals
- event/news catalysts

## Strategy Lab

- create/version strategy spec
- run backtest
- compare variants
- sensitivity analysis
- walk-forward results
- paper trading promotion status

## AI Copilot

Natural language interface for research, explanation and strategy exploration. It should be able to answer questions such as:
- Why did this stock move today?
- What changed in its fundamentals?
- Which stocks currently match this setup?
- Compare this strategy across regimes.
- Explain the risk in my portfolio.
- Propose a paper trade.

Live trade execution requires a separate explicit workflow; the copilot should never turn an ambiguous conversation into an order.
