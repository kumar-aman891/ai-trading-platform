# Market Data Rules

- Track source, timestamp, freshness, interval, exchange, instrument token, and adjustment status.
- Never mix adjusted and unadjusted series without explicit transformation metadata.
- Detect stale data, timestamp gaps, duplicates, impossible OHLC values, and corporate-action discontinuities.
- Canonical Indian trading data should prefer Zerodha/Kite for instruments and broker-account execution context.
- Secondary sources may enrich research but must not silently override the canonical trading price.
