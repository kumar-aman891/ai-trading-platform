# Backtesting and Paper Trading

## Two separate concepts

Backtest = historical simulation over stored data.

Paper trade = forward simulation using current/replayed market data and simulated fills.

Do not present either as equivalent to live performance.

## Event-driven simulator

The final validation engine should model:
- market sessions
- order submission latency
- limit-order non-fills
- partial fills
- spread/slippage
- fees and taxes
- position accounting
- short/derivative rules where applicable
- stop/target triggers
- corporate actions
- instrument expiry/roll for derivatives
- data availability at the exact event time

## Research workflow

Hypothesis -> strategy spec -> in-sample -> walk-forward -> out-of-sample -> stress -> paper -> constrained live.

## Anti-overfitting requirements

Report parameter sensitivity and out-of-sample results. Avoid selecting strategies solely on one optimized metric.

## Paper trading promotion gate

A strategy may be promoted only after:
- backtest passes validation
- no data leakage found
- expected risk is within limits
- paper trading run completed for a configured minimum period
- implementation/backtest parity verified
- operational failure scenarios tested
